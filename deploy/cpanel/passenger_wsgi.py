"""Entry point for cPanel "Setup Python App" (Phusion Passenger, WSGI).

The harness is an ASGI app (FastAPI); a2wsgi adapts it. Secrets stay in files outside the
web root (~/.config/ncsahub/*.env, mode 0600) and are never put in cPanel's app settings.
Copy or symlink this file to the app root (the repo checkout).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CONFIG = Path(os.environ.get("NCSAHUB_CONFIG", "~/.config/ncsahub")).expanduser()


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env(CONFIG / "db.env")
_load_env(CONFIG / "app.env")
os.environ.setdefault("STORE", "postgres")

from a2wsgi import ASGIMiddleware  # noqa: E402

from harness.api import create_app  # noqa: E402

application = ASGIMiddleware(create_app())
