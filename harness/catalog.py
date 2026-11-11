"""Turn Moodle web-service responses + shelves.yaml into the public catalog documents.

Output documents match contract/openapi.yaml exactly:
  meta        -> CatalogMeta
  categories  -> {"items": [Category]}
  shelves     -> {"items": [Shelf]}
  tiles       -> {"items": [Tile]}

Public means: a course Moodle shows to anonymous visitors, i.e. the course is visible AND its
category is visible. Only fields that are already public on Moodle are copied. Course contacts
(teacher names), enrolment details beyond "guest access", and anything about users are dropped.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

SUMMARY_MAX = 1200
LEVELS = {"intro", "intermediate", "advanced"}


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None, limit: int = SUMMARY_MAX) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def public_file_url(fileurl: str | None, site_url: str) -> str | None:
    """Web-service file URLs need a token; course overview images are public via /pluginfile.php."""
    if not fileurl:
        return None
    parts = urlsplit(fileurl)
    site = urlsplit(site_url)
    if parts.netloc != site.netloc or parts.scheme not in ("http", "https"):
        return None
    path = parts.path.replace("/webservice/pluginfile.php", "/pluginfile.php", 1)
    if "/pluginfile.php/" not in path:
        return None
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass
class BuildResult:
    meta: dict[str, Any]
    categories: dict[str, Any]
    shelves: dict[str, Any]
    tiles: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def documents(self) -> dict[str, Any]:
        return {"meta": self.meta, "categories": self.categories, "shelves": self.shelves, "tiles": self.tiles}


def load_shelves(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("shelves", [])
    cfg["groups"] = cfg.get("groups") or {}
    cfg["tools"] = cfg.get("tools") or {}
    cfg["overrides"] = cfg.get("overrides") or {}
    return cfg


def _ref_to_tile_id(ref: str) -> str:
    kind, _, key = str(ref).partition(":")
    key = key.strip()
    if kind == "course":
        return f"course-{int(key)}"
    if kind == "group":
        return f"group-{key}" if key else ""
    if kind == "tool":
        return f"tool-{key}"
    raise ValueError(f"unknown tile ref {ref!r} (use course:<id>, group:<key> or tool:<key>)")


def build_catalog(source: Any, shelves_cfg: dict[str, Any], now: datetime | None = None) -> BuildResult:
    site_url = source.site_url
    warnings: list[str] = []

    site_info = source.call("core_webservice_get_site_info")
    raw_categories = source.call("core_course_get_categories")
    raw_courses = source.call("core_course_get_courses_by_field").get("courses", [])

    # --- public categories (the token only sees visible ones; also honour an explicit flag if present)
    categories = {
        int(c["id"]): c for c in raw_categories if c.get("visible", 1) not in (0, "0", False)
    }

    # --- public courses
    courses: dict[int, dict[str, Any]] = {}
    for c in raw_courses:
        cid = int(c["id"])
        if cid <= 1:
            continue  # the site front page
        if c.get("visible", 1) in (0, "0", False):
            continue
        if int(c.get("categoryid", 0)) not in categories:
            continue  # visible course inside a hidden category is not public
        courses[cid] = c

    def course_url(cid: int) -> str:
        return f"{site_url}/course/view.php?id={cid}"

    def guest(c: dict[str, Any]) -> bool:
        return "guest" in (c.get("enrollmentmethods") or [])

    def image_of(c: dict[str, Any]) -> str | None:
        for f in c.get("overviewfiles") or []:
            if str(f.get("mimetype", "")).startswith("image/"):
                url = public_file_url(f.get("fileurl"), site_url)
                if url:
                    return url
        return None

    def category_ref(c: dict[str, Any]) -> dict[str, Any] | None:
        cat = categories.get(int(c.get("categoryid", 0)))
        return {"id": int(cat["id"]), "name": html_to_text(cat.get("name"), 200)} if cat else None

    tiles: dict[str, dict[str, Any]] = {}
    grouped: set[int] = set()

    # --- group tiles (programs / series)
    for key, g in shelves_cfg["groups"].items():
        members = [int(x) for x in g.get("courses", [])]
        public_members = [m for m in members if m in courses]
        for m in members:
            if m not in courses:
                warnings.append(f"group {key}: course {m} is not public; left out")
        if not public_members:
            warnings.append(f"group {key}: no public courses; tile skipped")
            continue
        primary = int(g.get("primary", public_members[0]))
        if primary not in courses:
            warnings.append(f"group {key}: primary course {primary} is not public; using {public_members[0]}")
            primary = public_members[0]
        grouped.update(public_members)
        pc = courses[primary]
        gtype = g.get("type", "program")
        tile_id = f"group-{key}"
        tiles[tile_id] = {
            "id": tile_id,
            "type": gtype if gtype in ("program", "course", "recording", "live") else "program",
            "title": str(g.get("title") or html_to_text(pc.get("fullname"), 200)),
            "summary": str(g.get("summary") or html_to_text(pc.get("summary"))),
            "launch": {"label": "Open program" if gtype == "program" else "Open course", "url": course_url(primary)},
            "guestAccess": guest(pc),
            "image": image_of(pc) or next((image_of(courses[m]) for m in public_members if image_of(courses[m])), None),
            "category": category_ref(pc),
            "level": None,
            "minutes": None,
            "shelfIds": [],
            "children": [
                {"title": html_to_text(courses[m].get("fullname"), 200), "url": course_url(m), "guestAccess": guest(courses[m])}
                for m in public_members
            ],
            "source": {"system": "moodle", "ids": public_members},
            "health": [],
        }

    # --- single-course tiles
    for cid, c in courses.items():
        if cid in grouped:
            continue
        tile_id = f"course-{cid}"
        tiles[tile_id] = {
            "id": tile_id,
            "type": "course",
            "title": html_to_text(c.get("displayname") or c.get("fullname"), 200),
            "summary": html_to_text(c.get("summary")),
            "launch": {"label": "Open course", "url": course_url(cid)},
            "guestAccess": guest(c),
            "image": image_of(c),
            "category": category_ref(c),
            "level": None,
            "minutes": None,
            "shelfIds": [],
            "source": {"system": "moodle", "ids": [cid]},
            "health": [],
        }

    # --- curated tool tiles
    for key, t in shelves_cfg["tools"].items():
        url = str(t.get("url", ""))
        if not url.startswith("https://"):
            warnings.append(f"tool {key}: url must start with https://; skipped")
            continue
        tile_id = f"tool-{key}"
        tiles[tile_id] = {
            "id": tile_id,
            "type": "tool",
            "title": str(t.get("title", key)),
            "summary": str(t.get("summary", "")),
            "launch": {"label": "Launch", "url": url},
            "guestAccess": True,
            "image": None,
            "category": None,
            "level": None,
            "minutes": None,
            "shelfIds": [],
            "source": {"system": "curated", "ids": []},
            "health": [],
        }

    # --- overrides
    for ref, ov in shelves_cfg["overrides"].items():
        tid = _ref_to_tile_id(ref)
        if tid not in tiles:
            warnings.append(f"override {ref}: no such public tile")
            continue
        level = (ov or {}).get("level")
        if level is not None:
            if level in LEVELS:
                tiles[tid]["level"] = level
            else:
                warnings.append(f"override {ref}: level must be one of {sorted(LEVELS)}")
        minutes = (ov or {}).get("minutes")
        if minutes is not None:
            tiles[tid]["minutes"] = int(minutes)
        summary = (ov or {}).get("summary")
        if summary is not None:
            # For courses whose Moodle summary is empty or unhelpful. Moodle stays the source otherwise.
            tiles[tid]["summary"] = " ".join(str(summary).split())

    # --- shelves
    shelves: list[dict[str, Any]] = []
    seen_shelves: set[str] = set()
    for s in shelves_cfg["shelves"]:
        sid = str(s["id"])
        if sid in seen_shelves:
            warnings.append(f"shelf {sid}: duplicate id; skipped")
            continue
        seen_shelves.add(sid)
        tile_ids: list[str] = []
        for ref in s.get("tiles") or []:
            tid = _ref_to_tile_id(ref)
            if tid.startswith("course-") and int(tid.split("-", 1)[1]) in grouped:
                warnings.append(f"shelf {sid}: {ref} belongs to a group; use the group ref instead")
                continue
            if tid not in tiles:
                warnings.append(f"shelf {sid}: {ref} is not a public course or known tile; left out")
                continue
            if tid in tile_ids:
                warnings.append(f"shelf {sid}: {ref} listed twice")
                continue
            tile_ids.append(tid)
            tiles[tid]["shelfIds"].append(sid)
        start = s.get("startHere")
        if isinstance(start, list):  # alternatives: the first one that is on this shelf
            start = next((r for r in start if _ref_to_tile_id(r) in tile_ids), start[0] if start else None)
        start_id = _ref_to_tile_id(start) if start else None
        if start_id and start_id not in tile_ids:
            warnings.append(f"shelf {sid}: startHere {start} is not on this shelf")
            start_id = None
        shelves.append(
            {
                "id": sid,
                "title": str(s.get("title", sid)),
                "purpose": str(s.get("purpose", "")),
                "tileIds": tile_ids,
                "startHere": start_id,
            }
        )

    # --- health
    health = {"noSummary": [], "noImage": [], "unshelved": []}
    for tid, t in tiles.items():
        if t["type"] == "tool":
            continue
        if not t["summary"]:
            t["health"].append("no-summary")
            health["noSummary"].append(tid)
        if not t["image"]:
            t["health"].append("no-image")
            health["noImage"].append(tid)
        if not t["shelfIds"]:
            t["health"].append("unshelved")
            health["unshelved"].append(tid)
            warnings.append(f"{tid} ({t['title']}) is public but on no shelf")

    # stable order: shelf order first, then everything else by title
    order: list[str] = []
    for s in shelves:
        for tid in s["tileIds"]:
            if tid not in order:
                order.append(tid)
    order += sorted((t for t in tiles if t not in order), key=lambda t: tiles[t]["title"].lower())
    tile_list = [tiles[t] for t in order]

    counts_per_cat: dict[int, int] = {}
    for c in courses.values():
        counts_per_cat[int(c["categoryid"])] = counts_per_cat.get(int(c["categoryid"]), 0) + 1
    category_list = [
        {
            "id": int(c["id"]),
            "name": html_to_text(c.get("name"), 200),
            "parentId": (int(c["parent"]) if int(c.get("parent") or 0) else None),
            "courseCount": counts_per_cat.get(int(c["id"]), 0),
        }
        for c in sorted(categories.values(), key=lambda c: (int(c.get("sortorder", 0)), int(c["id"])))
    ]

    now = now or datetime.now(timezone.utc)
    meta = {
        "schemaVersion": "1",
        "generatedAt": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": {
            "mode": source.mode,
            "siteUrl": site_url,
            "siteName": html_to_text(site_info.get("sitename"), 200),
            "release": str(site_info.get("release", "")),
        },
        "counts": {
            "courses": len(courses),
            "tiles": len(tile_list),
            "shelves": len(shelves),
            "categories": len(category_list),
        },
        "health": health,
        "warnings": warnings,
    }
    return BuildResult(
        meta=meta,
        categories={"items": category_list},
        shelves={"items": shelves},
        tiles={"items": tile_list},
        warnings=warnings,
    )
