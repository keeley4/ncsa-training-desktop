"""Catalog rules: what is public, what is copied, how shelves resolve."""
import json

from harness.catalog import html_to_text, public_file_url


def ids(result):
    return {t["id"] for t in result.tiles["items"]}


def test_only_public_courses(result):
    # 7 is in a hidden category, 8 is a hidden course, 1 is the site page
    assert result.meta["counts"]["courses"] == 6
    assert {"course-7", "course-8", "course-1"}.isdisjoint(ids(result))


def test_group_replaces_member_courses(result):
    assert "group-summer-school" in ids(result)
    assert {"course-5", "course-6"}.isdisjoint(ids(result))
    g = next(t for t in result.tiles["items"] if t["id"] == "group-summer-school")
    assert g["launch"]["url"].endswith("id=6")           # primary
    assert [c["title"] for c in g["children"]] == ["Summer School 2025", "Summer School 2024"]
    assert g["source"]["ids"] == [6, 5]
    assert g["image"]                                    # taken from the member that has one


def test_no_user_data_or_html(result):
    text = json.dumps(result.documents())
    assert "Pat Example" not in text                     # course contacts dropped
    assert "contacts" not in text and "enrollmentmethods" not in text
    assert "<" not in "".join(t["summary"] for t in result.tiles["items"])
    assert "alert(1)" not in text                        # script content removed
    assert "/webservice/" not in text and "wstoken" not in text


def test_guest_flag_and_launch(result):
    t = {t["id"]: t for t in result.tiles["items"]}
    assert t["course-2"]["guestAccess"] is True
    assert t["course-3"]["guestAccess"] is False
    assert t["course-2"]["launch"]["url"] == "https://moodle.example.org/course/view.php?id=2"


def test_shelves_and_warnings(result):
    shelves = {s["id"]: s for s in result.shelves["items"]}
    assert shelves["start"]["tileIds"] == ["course-2", "course-3"]
    assert shelves["start"]["startHere"] == "course-2"
    assert shelves["programming"]["tileIds"] == ["course-4", "group-summer-school"]
    assert shelves["programming"]["startHere"] is None
    w = "\n".join(result.warnings)
    for needle in ("course:7 is not a public", "course:8 is not a public", "course:5 belongs to a group",
                   "startHere course:99", "course-9 (Profiling Basics) is public but on no shelf"):
        assert needle in w


def test_health_flags(result):
    h = result.meta["health"]
    assert h["noSummary"] == ["course-3"]
    assert h["unshelved"] == ["course-9"]
    assert "tool-docs" not in h["noImage"]               # tools are not health-checked


def test_overrides(result):
    t = next(t for t in result.tiles["items"] if t["id"] == "course-2")
    assert (t["level"], t["minutes"]) == ("intro", 60)


def test_html_to_text():
    assert html_to_text("<p>A &amp; B</p><p>C</p>") == "A & B C"
    assert html_to_text(None) == ""
    long = html_to_text("word " * 600)
    assert len(long) <= 1200 and long.endswith("…")


def test_public_file_url():
    site = "https://learn.example.edu"
    assert public_file_url(f"{site}/webservice/pluginfile.php/5/course/overviewfiles/a.png?token=x", site) == \
        f"{site}/pluginfile.php/5/course/overviewfiles/a.png"
    assert public_file_url("https://evil.example.com/pluginfile.php/5/a.png", site) is None
    assert public_file_url(f"{site}/somewhere/else.png", site) is None
