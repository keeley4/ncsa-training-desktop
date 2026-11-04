"""Minimal Moodle web-services client (REST, JSON), standard library only.

The token is sent in the POST body, never in the URL, and never appears in errors or logs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol


class MoodleError(RuntimeError):
    def __init__(self, function: str, errorcode: str, message: str):
        super().__init__(f"{function}: {errorcode}: {message}")
        self.function = function
        self.errorcode = errorcode


class CatalogSource(Protocol):
    site_url: str
    mode: str

    def call(self, function: str, **params: Any) -> Any: ...


def _flatten(params: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Encode nested params the way Moodle's REST server expects (a[0][b]=c)."""
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out += _flatten(value, name)
        elif isinstance(value, (list, tuple)):
            out += _flatten({str(i): v for i, v in enumerate(value)}, name)
        elif isinstance(value, bool):
            out.append((name, "1" if value else "0"))
        else:
            out.append((name, str(value)))
    return out


class MoodleClient:
    mode = "live"

    def __init__(self, site_url: str, token: str, timeout: float = 60.0):
        if not token:
            raise ValueError("A Moodle web-services token is required for live mode.")
        self.site_url = site_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    @classmethod
    def from_env(cls, site_url: str) -> "MoodleClient":
        token = os.environ.get("MOODLE_TOKEN", "").strip()
        token_file = os.environ.get("MOODLE_TOKEN_FILE")
        if not token and token_file and Path(token_file).exists():
            token = Path(token_file).read_text().strip()
        return cls(site_url, token)

    def call(self, function: str, **params: Any) -> Any:
        body = urllib.parse.urlencode(
            [("wstoken", self._token), ("wsfunction", function), ("moodlewsrestformat", "json")]
            + _flatten(params)
        ).encode()
        req = urllib.request.Request(
            f"{self.site_url}/webservice/rest/server.php",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "ncsa-training-harness/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise MoodleError(function, f"http{exc.code}", exc.reason) from None
        except urllib.error.URLError as exc:
            raise MoodleError(function, "network", str(exc.reason)) from None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            raise MoodleError(function, "badjson", payload[:200].replace(self._token, "***")) from None
        if isinstance(data, dict) and "exception" in data:
            msg = str(data.get("message", "")).replace(self._token, "***")
            raise MoodleError(function, str(data.get("errorcode", "unknown")), msg)
        return data


class FixtureSource:
    """Reads saved web-service responses from a folder (for tests and offline builds)."""

    mode = "fixture"
    files = {
        "core_webservice_get_site_info": "site_info.json",
        "core_course_get_categories": "categories.json",
        "core_course_get_courses_by_field": "courses.json",
    }

    def __init__(self, folder: str | Path, site_url: str = "https://moodle.example.org"):
        self.folder = Path(folder)
        self.site_url = site_url.rstrip("/")

    def call(self, function: str, **params: Any) -> Any:
        name = self.files.get(function)
        if not name:
            raise MoodleError(function, "nofixture", "no fixture for this function")
        return json.loads((self.folder / name).read_text())
