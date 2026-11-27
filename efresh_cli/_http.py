"""Dependency-free HTTP transport for e-Fresh.gr.

e-Fresh speaks JSON on every endpoint this CLI uses, but the session is a
browser session: Laravel CSRF protection means the first request must load the
HTML page to learn the token. Everything after that is JSON POSTs carrying the
session cookie and the ``X-XSRF-TOKEN``/``X-CSRF-TOKEN`` headers.

A response is returned for any HTTP status, including 4xx/5xx; only a transport
failure (DNS, TLS, timeout) raises.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Optional

from . import __version__

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 efresh-cli/{__version__}"
)


class TransportError(RuntimeError):
    """The request never produced an HTTP response."""


class Response:
    __slots__ = ("status", "content_type", "text")

    def __init__(self, status: int, content_type: str, text: str) -> None:
        self.status = status
        self.content_type = content_type
        self.text = text

    @property
    def is_json(self) -> bool:
        return "json" in self.content_type.lower()

    def json(self) -> Any:
        return json.loads(self.text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Response {self.status} {self.content_type}>"


def request(
    url: str,
    *,
    method: str = "GET",
    json_body: Any = None,
    headers: Optional[dict[str, str]] = None,
    timeout: int = 40,
    jar: Optional[CookieJar] = None,
) -> Response:
    """Send one request and return the response without interpreting its status."""
    data: Optional[bytes] = None
    hdrs: dict[str, str] = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})

    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar if jar is not None else CookieJar())
    )
    try:
        with opener.open(req, timeout=timeout) as raw:
            return Response(
                raw.status,
                raw.headers.get("content-type") or "",
                raw.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as exc:
        content_type = exc.headers.get("content-type") if exc.headers else ""
        return Response(
            exc.code,
            content_type or "",
            exc.read().decode("utf-8", "replace"),
        )
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TransportError(f"cannot reach {url}: {reason}") from exc
