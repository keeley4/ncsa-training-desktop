"""Command line.

  python -m harness build  --mode live    --site https://learn.ncsa.illinois.edu --out dist
  python -m harness build  --mode fixture --fixtures fixtures/moodle --shelves fixtures/shelves.yaml --out dist
  python -m harness sync-db --mode live --site http://moodle.localhost:8080 --database-url postgresql://...

`build` writes the static site: web/ copied to <out>/, catalog JSON to <out>/api/v1/.
`sync-db` writes the same catalog into Postgres for the harness API.
Both validate every document against contract/openapi.yaml first and refuse to publish if it fails.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .catalog import build_catalog, load_shelves
from .contract import validate_documents
from .moodle import FixtureSource, MoodleClient, MoodleError

ROOT = Path(__file__).resolve().parent.parent


def _source(args: argparse.Namespace):
    if args.mode == "fixture":
        return FixtureSource(args.fixtures, site_url=args.site or "https://moodle.example.org")
    if not args.site:
        sys.exit("--site is required in live mode")
    return MoodleClient.from_env(args.site)


def _secrets() -> list[str]:
    out = [os.environ.get("MOODLE_TOKEN", "").strip()]
    tf = os.environ.get("MOODLE_TOKEN_FILE")
    if tf and Path(tf).exists():
        out.append(Path(tf).read_text().strip())
    return [s for s in out if len(s) >= 8]


def _report(result, path: Path | None) -> str:
    m = result.meta
    lines = [
        "# Catalog build report",
        "",
        f"- Source: {m['source']['siteName']} ({m['source']['siteUrl']}), Moodle {m['source']['release']}, mode `{m['source']['mode']}`",
        f"- Generated: {m['generatedAt']}",
        f"- Public courses: {m['counts']['courses']} · tiles: {m['counts']['tiles']} · shelves: {m['counts']['shelves']} · categories: {m['counts']['categories']}",
        f"- No summary: {len(m['health']['noSummary'])} · no image: {len(m['health']['noImage'])} · on no shelf: {len(m['health']['unshelved'])}",
        "",
        "## Warnings",
        "",
    ]
    lines += [f"- {w}" for w in result.warnings] or ["- none"]
    text = "\n".join(lines) + "\n"
    if path:
        path.write_text(text, encoding="utf-8")
    return text


def build_documents(args: argparse.Namespace):
    try:
        result = build_catalog(_source(args), load_shelves(args.shelves))
    except MoodleError as exc:
        sys.exit(f"Moodle call failed: {exc}")
    problems = validate_documents(result.documents())
    if problems:
        sys.exit("Contract validation failed:\n" + "\n".join(problems))
    if args.fail_on_warnings and result.warnings:
        sys.exit("Warnings present and --fail-on-warnings set:\n" + "\n".join(result.warnings))
    return result


def write_static(result, out: Path) -> None:
    api = out / "api" / "v1"
    (api / "catalog").mkdir(parents=True, exist_ok=True)
    (api / "tiles").mkdir(parents=True, exist_ok=True)
    dump = lambda p, d: p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    dump(api / "catalog" / "meta.json", result.meta)
    dump(api / "categories.json", result.categories)
    dump(api / "shelves.json", result.shelves)
    dump(api / "tiles.json", result.tiles)
    for t in result.tiles["items"]:
        dump(api / "tiles" / f"{t['id']}.json", t)
    shutil.copy(ROOT / "contract" / "openapi.yaml", api / "openapi.yaml")


def check_no_secrets(folder: Path) -> None:
    secrets = _secrets()
    for p in folder.rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            for s in secrets:
                if s.encode() in data:
                    sys.exit(f"Refusing to publish: a secret appears in {p}")
            if b"wstoken" in data or b"/webservice/" in data:
                sys.exit(f"Refusing to publish: web-service URL or token parameter in {p}")


def cmd_build(args: argparse.Namespace) -> None:
    result = build_documents(args)
    out = Path(args.out)
    if out.exists() and args.clean:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if not args.api_only:
        shutil.copytree(ROOT / "web", out, dirs_exist_ok=True)
        (out / ".nojekyll").write_text("")
    write_static(result, out)
    check_no_secrets(out)
    report = _report(result, Path(args.report) if args.report else None)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    print(report)


def cmd_sync_db(args: argparse.Namespace) -> None:
    from .store import PgStore

    result = build_documents(args)
    store = PgStore(args.database_url or os.environ["DATABASE_URL"])
    store.migrate()
    run_id = store.save(result.documents())
    print(_report(result, None))
    print(f"saved as sync run {run_id}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="harness")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "sync-db"):
        s = sub.add_parser(name)
        s.add_argument("--mode", choices=["live", "fixture"], default="live")
        s.add_argument("--site", default=os.environ.get("MOODLE_URL"))
        s.add_argument("--fixtures", default=str(ROOT / "fixtures" / "moodle"))
        s.add_argument("--shelves", default=str(ROOT / "catalog" / "shelves.yaml"))
        s.add_argument("--fail-on-warnings", action="store_true")
    b = sub.choices["build"]
    b.add_argument("--out", default="dist")
    b.add_argument("--api-only", action="store_true", help="write only api/v1, not the web files")
    b.add_argument("--clean", action="store_true", help="delete --out first")
    b.add_argument("--report", help="also write the build report (markdown) to this file")
    sub.choices["sync-db"].add_argument("--database-url")
    args = p.parse_args(argv)
    {"build": cmd_build, "sync-db": cmd_sync_db}[args.cmd](args)


if __name__ == "__main__":
    main()
