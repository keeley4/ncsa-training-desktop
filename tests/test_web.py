"""Static checks on the front door."""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def test_no_html_injection_sinks():
    js = (WEB / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, sink


def test_no_third_party_requests():
    for f in WEB.rglob("*"):
        if not f.is_file() or "fonts" in f.parts:  # self-hosted fonts and their licences
            continue
        text = f.read_text()
        assert "http://" not in text.replace("http://localhost", "").replace("http://www.w3.org/2000/svg", "")
        assert "googleapis" not in text and "cdn" not in text.lower()


def test_page_basics():
    html = (WEB / "index.html").read_text()
    assert '<html lang="en">' in html and 'name="viewport"' in html
    assert 'href="#main"' in html          # skip link
    assert "<dialog" in html and 'role="search"' in html


def test_fonts_are_self_hosted():
    css = (WEB / "styles.css").read_text()
    for name in ("montserrat-latin-700-normal", "montserrat-latin-800-normal", "source-sans-3-latin-400-normal",
                 "source-sans-3-latin-600-normal", "source-sans-3-latin-700-normal"):
        assert f"fonts/{name}.woff2" in css and (WEB / "fonts" / f"{name}.woff2").stat().st_size > 10_000
    assert (WEB / "fonts" / "OFL-Montserrat.txt").exists() and (WEB / "fonts" / "OFL-SourceSans3.txt").exists()
