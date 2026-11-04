"""The live client: token in the POST body only, errors never echo it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

from harness.moodle import MoodleClient, MoodleError, _flatten

TOKEN = "t0ken-abcdef-123456"
seen = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        q = parse_qs(body)
        seen.update(path=self.path, body=q)
        fn = q["wsfunction"][0]
        if fn == "boom":
            out = {"exception": "x", "errorcode": "invalidtoken", "message": f"bad {TOKEN}"}
        else:
            out = {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_token_in_body_not_url(server):
    c = MoodleClient(server, TOKEN)
    assert c.call("core_webservice_get_site_info") == {"ok": True}
    assert seen["path"] == "/webservice/rest/server.php"
    assert seen["body"]["wstoken"] == [TOKEN]
    assert seen["body"]["moodlewsrestformat"] == ["json"]


def test_errors_hide_token(server):
    with pytest.raises(MoodleError) as exc:
        MoodleClient(server, TOKEN).call("boom")
    assert exc.value.errorcode == "invalidtoken"
    assert TOKEN not in str(exc.value)


def test_flatten_nested_params():
    assert _flatten({"a": [{"b": 1}, {"b": True}], "c": "x"}) == [("a[0][b]", "1"), ("a[1][b]", "1"), ("c", "x")]
