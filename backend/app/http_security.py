from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _plain_response(status: int, detail: str, *, close: bool = False) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if close:
        headers.append((b"connection", b"close"))
    return body, headers


async def _send_error(send: Send, status: int, detail: str, *, close: bool = False) -> None:
    body, headers = _plain_response(status, detail, close=close)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    """Buffer a bounded request body before framework parsing begins.

    FastAPI/Starlette may parse JSON or multipart data before dependencies such as
    authentication run.  Bounding the ASGI byte stream here protects those public
    parsing paths as well as requests that omit Content-Length.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        timeout_seconds: float = 30.0,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.app = app
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        lengths: list[str] = []
        has_transfer_encoding = False
        try:
            for name, value in scope.get("headers", []):
                lowered = name.lower()
                if lowered == b"content-length":
                    lengths.append(value.decode("ascii", errors="strict").strip())
                elif lowered == b"transfer-encoding":
                    has_transfer_encoding = True
            if any(
                not value
                or len(value) > 20
                or not value.isascii()
                or not value.isdecimal()
                for value in lengths
            ):
                raise ValueError("invalid Content-Length grammar")
            parsed_lengths = {int(value) for value in lengths}
        except (UnicodeDecodeError, ValueError):
            await _send_error(send, 400, "Invalid Content-Length", close=True)
            return
        if any(value < 0 for value in parsed_lengths) or len(parsed_lengths) > 1:
            await _send_error(send, 400, "Invalid Content-Length", close=True)
            return
        if parsed_lengths and has_transfer_encoding:
            await _send_error(send, 400, "Ambiguous request framing", close=True)
            return
        if parsed_lengths and next(iter(parsed_lengths)) > self.max_bytes:
            await _send_error(send, 413, "Request body too large", close=True)
            return

        messages: list[Message] = []
        received = 0
        try:
            async with asyncio.timeout(self.timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        messages.append(message)
                        continue
                    received += len(message.get("body", b""))
                    if received > self.max_bytes:
                        await _send_error(send, 413, "Request body too large", close=True)
                        return
                    messages.append(message)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await _send_error(send, 408, "Request body timed out", close=True)
            return

        if parsed_lengths and received != next(iter(parsed_lengths)):
            await _send_error(send, 400, "Content-Length does not match body", close=True)
            return

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)


def _normalize_hostname(value: str) -> str | None:
    candidate = value.strip().rstrip(".")
    if (
        not candidate
        or len(candidate) > 253
        or any(character in candidate for character in "\r\n\t /\\@?#")
    ):
        return None
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        try:
            encoded = candidate.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None
        labels = encoded.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (character.isalnum() or character == "-") for character in label)
            for label in labels
        ):
            return None
        return encoded


def _host_from_header(value: str) -> str | None:
    value = value.strip()
    if (
        not value
        or len(value) > 300
        or any(character in value for character in "\r\n\t /\\")
    ):
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return None
        hostname = value[1:closing]
        remainder = value[closing + 1 :]
        if remainder:
            port_text = remainder[1:]
            if (
                not remainder.startswith(":")
                or not 1 <= len(port_text) <= 5
                or not port_text.isascii()
                or not port_text.isdecimal()
            ):
                return None
            port = int(port_text)
            if not 1 <= port <= 65535:
                return None
        try:
            return ipaddress.ip_address(hostname).compressed
        except ValueError:
            return None
    if value.count(":") > 1:
        return None
    hostname, separator, port_text = value.rpartition(":")
    if separator:
        if (
            not 1 <= len(port_text) <= 5
            or not port_text.isascii()
            or not port_text.isdecimal()
            or not 1 <= int(port_text) <= 65535
        ):
            return None
    else:
        hostname = value
    return _normalize_hostname(hostname)


def normalize_allowed_hosts(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        if value == "*":
            normalized.append(value)
            continue
        wildcard = value.startswith("*.")
        host_value = value[2:] if wildcard else value
        if host_value.startswith("[") and host_value.endswith("]"):
            host_value = host_value[1:-1]
        hostname = _normalize_hostname(host_value)
        if hostname is None or ("*" in value and not wildcard):
            raise ValueError(f"Invalid allowed host pattern: {raw}")
        if wildcard:
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                raise ValueError(f"Wildcard IP patterns are not supported: {raw}")
        normalized.append(f"*.{hostname}" if wildcard else hostname)
    if not normalized:
        raise ValueError("At least one allowed host is required")
    return tuple(dict.fromkeys(normalized))


class HostBoundaryMiddleware:
    """Reject untrusted Host values, including DNS-rebinding hostnames."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allowed_hosts = normalize_allowed_hosts(allowed_hosts)
        self.allow_any = "*" in self.allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host_values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"host"
        ]
        if len(host_values) != 1:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await _send_error(send, 400, "Invalid Host header", close=True)
            return
        host = _host_from_header(host_values[0])
        allowed = host is not None and (
            self.allow_any
            or any(
                host == pattern
                or (
                    pattern.startswith("*.")
                    and host.endswith(pattern[1:])
                    and host != pattern[2:]
                )
                for pattern in self.allowed_hosts
            )
        )
        if not allowed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await _send_error(send, 400, "Untrusted Host header", close=True)
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Apply browser hardening headers and disable caching of private APIs."""

    _HEADERS = (
        (b"content-security-policy", (
            b"default-src 'self'; base-uri 'self'; form-action 'self'; "
            b"frame-ancestors 'none'; object-src 'none'; script-src 'self'; "
            b"style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            b"font-src 'self'; connect-src 'self'; media-src 'self'; "
            b"worker-src 'self'; manifest-src 'self'"
        )),
        (b"referrer-policy", b"no-referrer"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (b"x-permitted-cross-domain-policies", b"none"),
    )

    def __init__(self, app: ASGIApp, *, api_prefix: str) -> None:
        self.app = app
        self.api_prefix = api_prefix.rstrip("/") + "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                for name, value in self._HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                path = str(scope.get("path", ""))
                if path == self.api_prefix[:-1] or path.startswith(self.api_prefix):
                    headers = [
                        (name, value)
                        for name, value in headers
                        if name.lower() not in {b"cache-control", b"pragma", b"expires"}
                    ]
                    headers.extend(
                        [
                            (b"cache-control", b"no-store"),
                            (b"pragma", b"no-cache"),
                            (b"expires", b"0"),
                        ]
                    )
                if scope.get("scheme") == "https" and b"strict-transport-security" not in existing:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, hardened)
