"""Static checks on the front door."""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def test_no_html_injection_sinks():
    js = (WEB / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, sink


def test_no_third_party_requests():
    for f in WEB.glob("*"):
        text = f.read_text()
        assert "http://" not in text.replace("http://localhost", "")
        assert "googleapis" not in text and "cdn" not in text.lower()


def test_page_basics():
    html = (WEB / "index.html").read_text()
    assert '<html lang="en">' in html and 'name="viewport"' in html
    assert 'href="#main"' in html          # skip link
    assert "<dialog" in html and 'role="search"' in html
