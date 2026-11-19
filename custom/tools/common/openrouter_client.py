"""custom/tools/common/openrouter_client.py — minimal OpenRouter HTTP client.

Protocol as verified on 2026-11-19 (no vendor SDK is installed here, so this is
stdlib urllib only):

  https://openrouter.ai/docs/guides/overview/multimodal/image-generation
      POST {base}/images         -> {"created", "data": [{"b64_json", "media_type"}],
                                    "usage": {"prompt_tokens", "completion_tokens",
                                              "total_tokens", "cost"}}
      GET  {base}/images/models  -> {"data": [{"id", "supported_parameters", ...}]}
                                    (live catalogue: 70 entries)

  https://openrouter.ai/docs/guides/features/compatibility
      "/api/v1/images and /api/v1/images/generations" are the same POST route.

Auth is ``Authorization: Bearer $OPENROUTER_API_KEY``. The key never reaches a
log line, an exception message or a repr: every message is rebuilt from URL,
status and body, and `_redact` scrubs the key as a second line of defence.

Smoke test (GET the catalogue; no key, no cost):

    python3 -m custom.tools.common.openrouter_client
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from typing import Any

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ENV_API_KEY = "OPENROUTER_API_KEY"
ENV_BASE_URL = "OPENROUTER_BASE_URL"

# 429 = rate limited, 5xx = provider side. 408/425 are the same "ask again later".
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_ERROR_SNIPPET = 400
MAX_MEDIA_BYTES = 32 * 1024 * 1024
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cost")

# opener(request, timeout, limit) -> (status, body); limit is a byte cap or None.
Opener = Callable[[urllib.request.Request, float, "int | None"], "tuple[int, bytes]"]


class OpenRouterError(Exception):
    """One failed call. `status` is the HTTP status when the server answered."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class MissingApiKey(OpenRouterError):
    """The call needs `OPENROUTER_API_KEY` and it is not set."""


def resolve_base_url(value: str | None = None) -> str:
    return (value or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def resolve_api_key(value: str | None = None) -> str | None:
    return (value or os.environ.get(ENV_API_KEY) or "").strip() or None


def _redact(text: str, key: str | None) -> str:
    """Strip the key and any bearer token from text that is about to be printed."""
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(?i)(bearer\s+)\S+", r"\1***", text)


def _urllib_opener(
    request: urllib.request.Request, timeout: float, limit: int | None = None
) -> tuple[int, bytes]:
    # https is the only scheme the API URL and check_media_url ever allow.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read() if limit is None else response.read(limit + 1)
        return response.status, body


def check_media_url(url: str) -> str:
    """Return the host of an acceptable media URL, else raise.

    Media URLs arrive inside an API response, so they are untrusted input: https
    only, no embedded credentials, and no local/private targets.
    """
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme != "https":
        raise OpenRouterError(
            f"refusing media URL with scheme {parts.scheme or '<none>'!r}; only https is allowed"
        )
    if parts.username or parts.password:
        raise OpenRouterError("refusing media URL with embedded credentials")
    host = (parts.hostname or "").strip("[]").lower()
    if not host:
        raise OpenRouterError("refusing media URL without a hostname")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise OpenRouterError(f"refusing media URL for local host {host!r}")
    # shortcut: hostnames are not resolved, so a DNS rebind onto a private address
    # is not caught; upgrade if media URLs ever come from untrusted users.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise OpenRouterError(f"refusing media URL for private address {host!r}")
    return host


def usage_report(payload: Any) -> dict[str, Any]:
    """The usage block the server actually sent. Never invents a cost."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {key: usage[key] for key in USAGE_KEYS if usage.get(key) is not None}


def format_report(meta: dict[str, Any], usage: dict[str, Any]) -> str:
    """One line: what was called, how long it took, what the server charged."""
    status = meta.get("status")
    head = (
        f"{meta.get('method', '?')} {meta.get('path', '?')} -> "
        f"{'no response' if status is None else status} in {float(meta.get('elapsed_s') or 0.0):.2f}s"
    )
    if meta.get("attempt"):
        head += f" (attempt {meta['attempt']})"
    if not usage:
        return head + " | usage: not reported by the server"
    parts = [f"{key}={usage[key]}" for key in USAGE_KEYS[:3] if key in usage]
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        parts.append(f"cost=${float(cost):.4f}")
    elif cost is not None:
        parts.append(f"cost={cost}")
    return head + " | usage: " + " ".join(parts)


class OpenRouterClient:
    """Bounded-retry JSON client for one OpenRouter base URL."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float = 180.0,
        max_attempts: int = 3,
        opener: Opener | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        self.base_url = resolve_base_url(base_url)
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self._opener = opener or _urllib_opener
        self._sleep = sleep

    def __repr__(self) -> str:  # the key must never show up in a traceback
        return f"OpenRouterClient(base_url={self.base_url!r}, key={'set' if self.api_key else 'unset'})"

    # -- requests -------------------------------------------------------

    def get_json(self, path: str, **kwargs: Any) -> tuple[Any, dict[str, Any]]:
        return self.request_json("GET", path, **kwargs)

    def post_json(
        self, path: str, payload: dict[str, Any], **kwargs: Any
    ) -> tuple[Any, dict[str, Any]]:
        return self.request_json("POST", path, payload=payload, **kwargs)

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        timeout: float | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """Return (parsed body, meta). meta carries status, elapsed and attempt."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.api_key:
                raise MissingApiKey(
                    f"{ENV_API_KEY} is not set; nothing was sent to {method} {url}"
                )
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(  # noqa: S310 — scheme comes from the base URL
            url, data=body, headers=headers, method=method
        )
        return self._with_retry(request, method, path, self.timeout if timeout is None else float(timeout))

    def post_raw(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        max_bytes: int = MAX_MEDIA_BYTES,
        timeout: float | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """POST and return the body as bytes — the speech API replies with audio.

        A success body is never parsed as JSON; only a non-2xx body is read, and
        only to quote the server's JSON error message. Bytes are capped so a
        runaway response cannot exhaust memory.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        if not self.api_key:
            raise MissingApiKey(f"{ENV_API_KEY} is not set; nothing was sent to POST {url}")
        request = urllib.request.Request(  # noqa: S310 — scheme comes from the base URL
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/*",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        data, meta = self._with_retry(
            request,
            "POST",
            path,
            self.timeout if timeout is None else float(timeout),
            as_bytes=True,
            limit=max_bytes,
        )
        if len(data) > max_bytes:
            raise OpenRouterError(
                f"response from POST {url} exceeds the {max_bytes} byte limit"
            )
        return data, meta

    def download_media(self, url: str, *, max_bytes: int = MAX_MEDIA_BYTES) -> bytes:
        """Fetch one media URL. Cap in bytes, https only, and never with the API key."""
        host = check_media_url(url)
        request = urllib.request.Request(url, headers={"Accept": "image/*"})  # noqa: S310 — https enforced above
        status, raw = self._send(request, self.timeout, limit=max_bytes)
        if not 200 <= status < 300:
            raise OpenRouterError(
                f"HTTP {status} downloading media from {host}: {self._snippet(raw)}", status=status
            )
        if len(raw) > max_bytes:
            raise OpenRouterError(
                f"media from {host} exceeds the {max_bytes} byte limit", retryable=False
            )
        return raw

    # -- internals ------------------------------------------------------

    def _with_retry(
        self,
        request: urllib.request.Request,
        method: str,
        path: str,
        timeout: float,
        *,
        as_bytes: bool = False,
        limit: int | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                status, body = self._send(request, timeout, limit)
                if 200 <= status < 300:
                    decoded = body if as_bytes else self._decode(body, request.full_url, status)
                    return decoded, {
                        "method": method,
                        "path": path,
                        "url": request.full_url,
                        "status": status,
                        "attempt": attempt,
                        "elapsed_s": round(time.monotonic() - started, 3),
                        "mode": "live",
                    }
                raise OpenRouterError(
                    f"HTTP {status} from {method} {request.full_url}: {self._snippet(body)}",
                    status=status,
                    retryable=status in RETRY_STATUS or status >= 500,
                )
            except OpenRouterError as exc:
                if not exc.retryable or attempt >= self.max_attempts:
                    raise
                delay = min(2 ** (attempt - 1), 8)
                print(
                    f"[openrouter] attempt {attempt}/{self.max_attempts} failed "
                    f"({exc}); retrying in {delay}s",
                    file=sys.stderr,
                )
                self._sleep(delay)
        raise OpenRouterError(f"no attempt completed for {method} {request.full_url}")

    def _send(
        self, request: urllib.request.Request, timeout: float, limit: int | None = None
    ) -> tuple[int, bytes]:
        try:
            return self._opener(request, timeout, limit)
        except urllib.error.HTTPError as exc:  # urlopen raises for 4xx/5xx
            try:
                return exc.code, exc.read() or b""
            finally:
                exc.close()
        except urllib.error.URLError as exc:
            where = f"{request.get_method()} {request.full_url}"
            if isinstance(exc.reason, TimeoutError):
                raise OpenRouterError(
                    f"timed out after {timeout:g}s for {where}", retryable=True
                ) from None
            raise OpenRouterError(f"network error for {where}: {exc.reason}", retryable=True) from None
        except TimeoutError:
            raise OpenRouterError(
                f"timed out after {timeout:g}s for {request.get_method()} {request.full_url}",
                retryable=True,
            ) from None
        except (http.client.HTTPException, ConnectionError) as exc:
            # RemoteDisconnected, BadStatusLine, resets: urlopen lets these escape
            # uncaught, so without this a dropped connection is a traceback.
            raise OpenRouterError(
                f"connection failed for {request.get_method()} {request.full_url}: "
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            ) from None

    def _decode(self, raw: bytes, url: str, status: int) -> Any:
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise OpenRouterError(
                f"invalid JSON from {url}: {exc}: {self._snippet(raw)}", status=status
            ) from None

    def _snippet(self, raw: bytes) -> str:
        text = " ".join(raw.decode("utf-8", "replace").split())
        return _redact(text[:MAX_ERROR_SNIPPET], self.api_key) or "<empty body>"


def main(argv: Iterable[str] | None = None) -> int:
    """GET one read-only path and print its status, elapsed time and usage."""
    parser = argparse.ArgumentParser(
        prog="python3 -m custom.tools.common.openrouter_client",
        description="Call one OpenRouter GET path and report status, elapsed time and usage.",
    )
    parser.add_argument("--path", default="images/models", help="path under the base URL")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--dump", action="store_true", help="print the raw JSON response")
    args = parser.parse_args(argv)

    client = OpenRouterClient(timeout=args.timeout, max_attempts=args.max_attempts)
    print(f"base_url: {client.base_url}")
    print(f"api_key: {'set' if client.api_key else 'unset'}")
    try:
        data, meta = client.get_json(args.path, auth=False)
    except OpenRouterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(meta, usage_report(data)))
    items = data.get("data") if isinstance(data, dict) else None
    if isinstance(items, list):
        print(f"items: {len(items)}")
    if args.dump:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
