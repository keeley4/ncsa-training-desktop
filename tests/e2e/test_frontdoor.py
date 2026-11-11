"""Browser tests for the front door: keyboard flows, routing, pins, filters, and axe accessibility checks.

Runs against a fixture build served locally. Skipped if Playwright's Chromium is not installed
(CI installs it: `python -m playwright install --with-deps chromium`).
"""
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from axe_playwright_python.sync_playwright import Axe  # noqa: E402

from harness.__main__ import main  # noqa: E402
from conftest import ROOT  # noqa: E402


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("e2e")
    main(["build", "--mode", "fixture", "--fixtures", str(ROOT / "fixtures" / "moodle"),
          "--shelves", str(ROOT / "fixtures" / "shelves.yaml"), "--out", str(out)])
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(out))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, site):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.route("**/moodle.example.org/**", lambda r: r.abort())  # fixture images point at a fake host
    pg.goto(site)
    pg.wait_for_selector("#view .tile")
    yield pg
    assert pg.errors == []
    ctx.close()


def hash_of(page):
    return page.evaluate("location.hash")


def violations(page):
    res = Axe().run(page)
    return [f"{v['id']} ({v['impact']}): {v['help']} — {[n['target'] for n in v['nodes']][:3]}"
            for v in res.response["violations"]]


def test_home_shows_shelves_and_footer(page):
    assert page.locator("#view .shelf h2").all_inner_texts() == ["Get started", "Programming"]
    assert "6 courses from Learn@NCSA (dev)" in page.inner_text("#footer-meta")
    assert page.locator(".tile[data-tile='course-2'] .badge.start").count() == 1
    assert page.inner_text(".tile[data-tile='course-2'] .tile-cat").endswith("No sign-in")


def test_one_tab_stop_per_row_and_arrow_keys(page):
    tabbable = page.locator("#view .tile[tabindex='0']")
    assert tabbable.count() == 2  # one per shelf
    page.focus(".tile[data-tile='course-2']")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.dataset.tile") == "course-3"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.closest('.shelf').querySelector('h2').textContent") == "Programming"
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.dataset.tile") == "group-summer-school"
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.dataset.tile") == "course-4"


def test_sheet_open_close_returns_focus_and_history(page):
    page.focus(".tile[data-tile='course-2']")
    page.keyboard.press("Enter")
    page.wait_for_selector("#sheet[open]")
    assert hash_of(page) == "#/tile/course-2"
    assert page.inner_text("#sheet-title") == "Intro to the Command Line"
    assert "No sign-in needed" in page.inner_text("#sheet-meta")
    assert page.get_attribute("#sheet-launch", "href") == "https://moodle.example.org/course/view.php?id=2"
    assert page.evaluate("document.activeElement.id") == "sheet-title"
    page.keyboard.press("Escape")
    page.wait_for_selector("#sheet:not([open])", state="attached")
    assert hash_of(page) in ("", "#/")
    assert page.evaluate("document.activeElement.dataset.tile") == "course-2"


def test_deep_link_and_back_button(page, site):
    page.goto(site + "#/tile/group-summer-school")
    page.wait_for_selector("#sheet[open]")
    assert page.locator("#sheet-children-list li").count() == 2
    page.goto(site + "#/tile/course-999")
    page.wait_for_selector("#sheet:not([open])", state="attached")
    assert "not in the catalog" in page.inner_text("#status")


def test_browser_back_closes_sheet(page):
    page.click(".tile[data-tile='course-3']")
    page.wait_for_selector("#sheet[open]")
    page.go_back()
    page.wait_for_selector("#sheet:not([open])", state="attached")


def test_search_filters_and_clear(page):
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "q"
    page.keyboard.type("mpi")
    page.wait_for_selector("#h-results")
    assert [t.get_attribute("data-tile") for t in page.locator("#view .tile").all()] == ["course-4"]
    page.keyboard.press("Escape")
    page.wait_for_selector("#view .shelf h2:text('Get started')")
    page.check("#f-open")
    ids = sorted(t.get_attribute("data-tile") for t in page.locator("#view .tile").all())
    assert ids == ["course-2", "course-9", "tool-docs"]
    assert "no sign-in only" in page.inner_text("#view .purpose")
    page.fill("#q", "zzzz")
    page.wait_for_selector("#view .empty")
    assert "Nothing matches" in page.inner_text("#view")
    page.click("text=Clear search and filters")
    assert not page.is_checked("#f-open")


def test_tiles_say_whether_sign_in_is_needed(page):
    # every course and program tile says it, so nobody finds out at the Moodle enrol page
    for t in page.locator("#view .tile").all():
        line = t.locator(".tile-cat").inner_text() if t.locator(".tile-cat").count() else ""
        if t.get_attribute("data-tile").startswith("tool-"):
            continue
        assert line.endswith("No sign-in") or line.endswith("Sign in required"), line
    # header has no Courses/Programs/Tracks controls and home has no "See all" links
    assert page.locator(".chip[data-filter], #tracks, .see-all").count() == 0
    assert page.locator(".tile-art").count() == 0


def test_shelf_view_is_a_deep_link(page, site):
    page.goto(site + "#/shelf/programming")
    page.wait_for_selector(".crumb")
    assert page.inner_text(".crumb") == "← All courses"
    assert page.locator("#view .tiles.grid .tile").count() == 2


def test_pins_persist(page, site):
    page.click(".tile[data-tile='course-4']")
    page.click("#sheet-pin")
    assert page.get_attribute("#sheet-pin", "aria-pressed") == "true"
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_selector("#h-mydesktop")
    pinned = page.locator("section[aria-labelledby='h-mydesktop'] .tile")
    assert [t.get_attribute("data-tile") for t in pinned.all()] == ["course-4"]
    assert page.locator(".tile[data-tile='course-4'] .badge.pinned").count() == 2  # My desktop + its own shelf


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1280, 375])
def test_accessibility_axe(browser, site, scheme, width):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    pg = ctx.new_page()
    pg.route("**/moodle.example.org/**", lambda r: r.abort())
    pg.goto(site)
    pg.wait_for_selector("#view .tile")
    problems = {"home": violations(pg)}
    assert not pg.evaluate("document.documentElement.scrollWidth > window.innerWidth"), "horizontal page scroll"
    pg.goto(site + "#/tile/group-summer-school")
    pg.wait_for_selector("#sheet[open]")
    problems["sheet"] = violations(pg)
    pg.goto(site + "#/shelf/programming")
    pg.wait_for_selector(".crumb")
    problems["shelf"] = violations(pg)
    pg.goto(site + "curation.html")
    pg.wait_for_selector("#stats li")
    problems["curation"] = violations(pg)
    ctx.close()
    assert {k: v for k, v in problems.items() if v} == {}


def test_curation_page(page, site):
    page.goto(site + "curation.html")
    page.wait_for_selector("#stats li")
    text = page.inner_text("main")
    assert "On no track (1)" in text and "Profiling Basics" in text
    assert "No description (1)" in text and "Cluster Basics" in text
    assert page.locator("a[href='https://moodle.example.org/course/edit.php?id=9']").count() >= 1
    assert page.get_attribute("#edit a", "href").endswith("/edit/main/catalog/shelves.yaml")
