"""Globus sign-in: login redirect, callback checks, sessions, /me, logout. Globus is faked."""
import base64
import json
import os
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from harness import auth
from harness.api import create_app
from harness.auth import AuthConfig, MemoryAuthStore, check_id_token, safe_next
from harness.contract import errors
from harness.store import FileStore

CFG = AuthConfig(client_id="client-123", client_secret="s3cret-test-value", base_url="https://training.example.edu")


def jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS512'})}.{enc(claims)}.sig"


def claims_for(nonce, **over):
    c = {
        "iss": "https://auth.globus.org", "aud": "client-123", "sub": "globus-sub-1",
        "exp": time.time() + 3600, "iat": time.time(), "nonce": nonce,
        "name": "Ada Lovelace", "email": "ada@illinois.edu", "preferred_username": "ada@illinois.edu",
        "organization": "University of Illinois", "identity_provider_display_name": "University of Illinois",
        "identity_set": [
            {"sub": "globus-sub-1", "username": "ada@illinois.edu", "email": "ada@illinois.edu",
             "identity_provider_display_name": "University of Illinois", "organization": "University of Illinois"},
            {"sub": "globus-sub-2", "username": "ada@orcid.org", "identity_provider_display_name": "ORCID"},
        ],
    }
    c.update(over)
    return c


class FakeGlobus:
    def __init__(self):
        self.calls = []
        self.claims_override = {}

    def __call__(self, cfg, code, verifier):
        self.calls.append((code, verifier))
        over = dict(self.claims_override)
        return {"access_token": "at", "id_token": jwt(claims_for(over.pop("nonce", self.nonce), **over))}


@pytest.fixture
def env(built_site):
    store = MemoryAuthStore()
    fake = FakeGlobus()
    app = create_app(FileStore(built_site / "api" / "v1"), auth_store=store, auth_config=CFG, exchange=fake)
    client = TestClient(app, base_url="https://training.example.edu")
    return client, store, fake


def start_login(client, fake, next_path="/desk"):
    r = client.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 302
    url = urllib.parse.urlparse(r.headers["location"])
    q = dict(urllib.parse.parse_qsl(url.query))
    fake.nonce = q["nonce"]
    return url, q


def test_login_redirect_has_pkce_state_nonce(env):
    client, store, fake = env
    url, q = start_login(client, fake)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://auth.globus.org/v2/oauth2/authorize"
    assert q["client_id"] == "client-123" and q["response_type"] == "code"
    assert q["redirect_uri"] == "https://training.example.edu/auth/callback"
    assert q["scope"] == "openid profile email"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert "s3cret" not in url.query


def test_full_signin_me_and_logout(env):
    client, store, fake = env
    assert client.get("/api/v1/me").json() == {"signedIn": False}
    _, q = start_login(client, fake, "/desk")
    r = client.get("/auth/callback", params={"state": q["state"], "code": "abc"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/desk"
    cookie = r.headers["set-cookie"]
    for part in ("__Host-ncsa_session=", "HttpOnly", "Secure", "Path=/", "SameSite=lax"):
        assert part.lower() in cookie.lower(), part
    # the PKCE verifier sent to Globus matches the challenge sent to the browser
    code, verifier = fake.calls[0]
    assert code == "abc" and auth._b64url(__import__("hashlib").sha256(verifier.encode()).digest()) == q["code_challenge"]

    me = client.get("/api/v1/me")
    body = me.json()
    assert me.headers["cache-control"] == "no-store"
    assert body["signedIn"] is True and body["user"]["username"] == "ada@illinois.edu"
    assert [i["username"] for i in body["user"]["identities"]] == ["ada@illinois.edu", "ada@orcid.org"]
    assert errors("me", body) == []
    assert errors("me", {"signedIn": False}) == []
    # only a hash of the cookie is stored
    raw = client.cookies.get("__Host-ncsa_session")
    assert raw not in store.sessions and auth.hash_session(raw) in store.sessions

    r = client.post("/auth/logout", follow_redirects=False)
    assert r.status_code == 303
    assert store.sessions == {}
    client.cookies.clear()
    assert client.get("/api/v1/me").json() == {"signedIn": False}


def test_state_is_single_use_and_required(env):
    client, store, fake = env
    _, q = start_login(client, fake)
    assert client.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False).status_code == 303
    again = client.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False)
    assert again.status_code == 400
    assert client.get("/auth/callback", params={"state": "made-up", "code": "a"}).status_code == 400
    assert client.get("/auth/callback", params={"code": "a"}).status_code == 400


def test_globus_error_is_not_a_signin(env):
    client, store, fake = env
    _, q = start_login(client, fake)
    r = client.get("/auth/callback", params={"state": q["state"], "error": "access_denied"})
    assert r.status_code == 400 and fake.calls == [] and store.sessions == {}


@pytest.mark.parametrize("override,reason", [
    ({"iss": "https://evil.example"}, "issuer"), ({"aud": "someone-else"}, "audience"),
    ({"exp": time.time() - 3600}, "expired"), ({"nonce": "replayed"}, "nonce"),
])
def test_bad_id_token_rejected(env, override, reason):
    client, store, fake = env
    _, q = start_login(client, fake)
    fake.claims_override = override
    r = client.get("/auth/callback", params={"state": q["state"], "code": "a"})
    assert r.status_code == 502 and reason in r.json()["message"] and store.sessions == {}


def test_expired_session_is_signed_out(env):
    client, store, fake = env
    _, q = start_login(client, fake)
    client.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False)
    for k, (uid, _) in list(store.sessions.items()):
        store.sessions[k] = (uid, auth._now())
    assert client.get("/api/v1/me").json() == {"signedIn": False}


def test_unconfigured_signin(built_site, monkeypatch):
    for k in ("GLOBUS_CLIENT_ID", "GLOBUS_CLIENT_SECRET", "GLOBUS_CLIENT_SECRET_FILE", "PUBLIC_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    client = TestClient(create_app(FileStore(built_site / "api" / "v1")), base_url="https://x.edu")
    assert client.get("/auth/login", follow_redirects=False).status_code == 503
    assert client.get("/api/v1/me").json() == {"signedIn": False}


def test_config_reads_secret_from_file(tmp_path, monkeypatch):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    monkeypatch.setenv("GLOBUS_CLIENT_ID", "cid")
    monkeypatch.delenv("GLOBUS_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("GLOBUS_CLIENT_SECRET_FILE", str(f))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://h.edu/")
    cfg = AuthConfig.from_env()
    assert cfg.client_secret == "from-file" and cfg.redirect_uri == "https://h.edu/auth/callback"


@pytest.mark.parametrize("value,expected", [
    ("/desk", "/desk"), ("/api/v1/me", "/api/v1/me"), (None, "/auth"), ("", "/auth"),
    ("//evil.example", "/auth"), ("https://evil.example", "/auth"), ("/\\evil", "/auth"), ("desk", "/auth"),
])
def test_safe_next(value, expected):
    assert safe_next(value) == expected


def test_check_id_token_accepts_list_audience():
    check_id_token(claims_for("n", aud=["x", "client-123"]), CFG, "n")


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (Postgres)")
def test_postgres_auth_store(result):
    from harness.store import PgStore
    store = PgStore(os.environ["TEST_DATABASE_URL"])
    store.migrate()
    store.save(result.documents())
    fake = FakeGlobus()
    client = TestClient(create_app(store, auth_config=CFG, exchange=fake), base_url="https://training.example.edu")
    for _ in range(2):  # second login updates, does not duplicate
        _, q = start_login(client, fake)
        assert client.get("/auth/callback", params={"state": q["state"], "code": "a"},
                          follow_redirects=False).status_code == 303
    body = client.get("/api/v1/me").json()
    assert body["signedIn"] and len(body["user"]["identities"]) == 2
    assert errors("me", body) == []
    client.post("/auth/logout", follow_redirects=False)
    client.cookies.clear()
    assert client.get("/api/v1/me").json() == {"signedIn": False}


def test_status_page(env):
    client, store, fake = env
    assert "Sign in with Globus" in client.get("/auth").text
    _, q = start_login(client, fake, "/auth")
    fake.claims_override = {"name": "<script>x</script>"}
    client.get("/auth/callback", params={"state": q["state"], "code": "a"}, follow_redirects=False)
    page = client.get("/auth").text
    assert "&lt;script&gt;" in page and "<script>x" not in page and "ada@orcid.org" in page
