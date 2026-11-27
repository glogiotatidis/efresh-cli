"""Session storage for e-Fresh.gr.

e-Fresh authenticates with a Laravel session cookie, not OAuth: a successful
``POST /api/account/login`` returns ``Set-Cookie`` and the browser simply keeps
sending it. This module keeps those cookies on disk under
``~/.config/efresh-cli/`` (mode 0600, override the directory with
``EFRESH_CONFIG_DIR``) so one login lasts until the session expires.

The file holds the equivalent of a logged-in browser: treat it as a secret.
No password is ever stored.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Any, Optional

CONFIG_DIR_ENV = "EFRESH_CONFIG_DIR"
SESSION_FILE = "session.json"


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "efresh-cli"


def session_path() -> Path:
    return config_dir() / SESSION_FILE


@dataclass
class Session:
    email: Optional[str] = None
    cookies: list[dict[str, Any]] = field(default_factory=list)

    # -- disk ------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Session":
        target = path or session_path()
        try:
            raw = json.loads(target.read_text("utf-8"))
        except FileNotFoundError:
            return cls()
        except (json.JSONDecodeError, OSError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        cookies = raw.get("cookies")
        email = raw.get("email")
        return cls(
            email=email if isinstance(email, str) else None,
            cookies=cookies if isinstance(cookies, list) else [],
        )

    def save(self, path: Optional[Path] = None) -> None:
        target = path or session_path()
        # Only tighten the directory when this call creates it. EFRESH_CONFIG_DIR
        # may point at an existing shared directory; do not rewrite its mode.
        created = not target.parent.exists()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            try:
                os.chmod(target.parent, 0o700)
            except OSError:
                pass
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"email": self.email, "cookies": self.cookies},
                       indent=2, ensure_ascii=False),
            "utf-8",
        )
        os.chmod(tmp, 0o600)
        tmp.replace(target)

    @classmethod
    def clear(cls, path: Optional[Path] = None) -> bool:
        target = path or session_path()
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return False

    # -- cookies ---------------------------------------------------------
    def jar(self) -> CookieJar:
        jar = CookieJar()
        for raw in self.cookies:
            try:
                jar.set_cookie(_cookie_from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return jar

    def adopt(self, jar: CookieJar) -> None:
        self.cookies = [_cookie_to_dict(cookie) for cookie in jar]


def _cookie_to_dict(cookie: Cookie) -> dict[str, Any]:
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path,
        "secure": bool(cookie.secure),
        "expires": cookie.expires,
    }


def _cookie_from_dict(raw: dict[str, Any]) -> Cookie:
    domain = str(raw.get("domain") or "www.e-fresh.gr")
    expires = raw.get("expires")
    return Cookie(
        version=0,
        name=str(raw["name"]),
        value=str(raw["value"]),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=bool(domain),
        domain_initial_dot=domain.startswith("."),
        path=str(raw.get("path") or "/"),
        path_specified=True,
        secure=bool(raw.get("secure")),
        expires=int(expires) if expires else None,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )
