"""Secret lookup shared by the backend and the workers (spec §15.17, Docker/Compose secrets).

A secret `NAME` is resolved in this order:

1. `NAME_FILE` — path to a file holding the value (the Docker / Kubernetes convention);
2. `NAME` — plain environment variable (development, `.env`);
3. `$SECRETS_DIR/name` (default `/run/secrets/name`, lower-case) — Compose `secrets:` mounts;
4. the default.

Values read from files are stripped of surrounding whitespace (editors add a trailing newline).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

SECRETS_DIR_ENV = "SECRETS_DIR"
DEFAULT_SECRETS_DIR = "/run/secrets"


def _read(path: str | os.PathLike[str]) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def file_secret(name: str, environ: dict[str, str] | None = None) -> str:
    """Only the file-based sources (`NAME_FILE`, then the secrets directory); "" when absent."""
    env = os.environ if environ is None else environ
    file_path = env.get(f"{name}_FILE")
    if file_path:
        value = _read(file_path)
        if value is None:
            raise RuntimeError(f"{name}_FILE points to an unreadable file")
        return value
    return _read(Path(env.get(SECRETS_DIR_ENV, DEFAULT_SECRETS_DIR)) / name.lower()) or ""


def env_secret(name: str, default: str = "", environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    if env.get(f"{name}_FILE"):
        return file_secret(name, env)
    if env.get(name):
        return env[name]
    return file_secret(name, env) or default


def with_password(url: str, password: str) -> str:
    """Put `password` into a `scheme://user[:old]@host/...` URL (URL-encoded); other URLs unchanged."""
    if not password or "://" not in url:
        return url
    parts = urlsplit(url)
    if not parts.username or not parts.hostname:
        return url
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    netloc = f"{quote(parts.username, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
