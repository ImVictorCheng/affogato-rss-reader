from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

import pytest

from backend.app.config import Settings
from backend.app.http_security import (
    HostBoundaryMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    normalize_allowed_hosts,
)


def _scope(
    *,
    path: str = "/",
    scheme: str = "http",
    headers: Iterable[tuple[bytes, bytes]] = (),
) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.5"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8787),
    }


def _invoke(app, scope: dict, messages: list[dict]) -> list[dict]:
    output: list[dict] = []

    async def run() -> None:
        queue = iter(messages)

        async def receive() -> dict:
            return next(queue, {"type": "http.disconnect"})

        async def send(message: dict) -> None:
            output.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    return output


def _status(output: list[dict]) -> int:
    return next(message["status"] for message in output if message["type"] == "http.response.start")


def _body(output: list[dict]) -> dict:
    payload = b"".join(
        message.get("body", b"")
        for message in output
        if message["type"] == "http.response.body"
    )
    return json.loads(payload)


def test_request_limit_rejects_declared_oversize_before_calling_app() -> None:
    called = False

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal called
        called = True

    app = RequestBodyLimitMiddleware(downstream, max_bytes=4)
    output = _invoke(
        app,
        _scope(headers=[(b"host", b"localhost"), (b"content-length", b"5")]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(output) == 413
    assert _body(output) == {"detail": "Request body too large"}
    assert called is False


def test_request_limit_counts_stream_without_content_length_and_replays_valid_body() -> None:
    received = b""

    async def downstream(_scope, receive, send) -> None:
        nonlocal received
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = RequestBodyLimitMiddleware(downstream, max_bytes=4)
    messages = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": False},
    ]
    output = _invoke(app, _scope(headers=[(b"host", b"localhost")]), messages)

    assert _status(output) == 204
    assert received == b"abcd"


def test_request_limit_rejects_stream_that_crosses_limit() -> None:
    async def downstream(_scope, _receive, _send) -> None:  # pragma: no cover
        raise AssertionError("oversize body reached the application")

    app = RequestBodyLimitMiddleware(downstream, max_bytes=4)
    messages = [
        {"type": "http.request", "body": b"abcd", "more_body": True},
        {"type": "http.request", "body": b"e", "more_body": False},
    ]
    output = _invoke(app, _scope(headers=[(b"host", b"localhost")]), messages)

    assert _status(output) == 413


def test_request_limit_times_out_slow_body_before_calling_app() -> None:
    called = False
    output: list[dict] = []

    async def downstream(_scope, _receive, _send) -> None:
        nonlocal called
        called = True

    async def receive() -> dict:
        await asyncio.sleep(0.05)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        output.append(message)

    app = RequestBodyLimitMiddleware(
        downstream,
        max_bytes=4,
        timeout_seconds=0.01,
    )
    asyncio.run(app(_scope(headers=[(b"host", b"localhost")]), receive, send))

    assert _status(output) == 408
    assert _body(output) == {"detail": "Request body timed out"}
    assert called is False


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"x")],
        [(b"content-length", b"+1")],
        [(b"content-length", b"1_0")],
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
    ],
)
def test_request_limit_rejects_ambiguous_framing(headers: list[tuple[bytes, bytes]]) -> None:
    async def downstream(_scope, _receive, _send) -> None:  # pragma: no cover
        raise AssertionError("invalid request reached the application")

    app = RequestBodyLimitMiddleware(downstream, max_bytes=4)
    output = _invoke(
        app,
        _scope(headers=[(b"host", b"localhost"), *headers]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(output) == 400


@pytest.mark.parametrize(
    ("declared", "body"),
    [
        (b"0", b"x"),
        (b"10", b"x"),
    ],
)
def test_request_limit_rejects_content_length_body_mismatch(
    declared: bytes, body: bytes
) -> None:
    async def downstream(_scope, _receive, _send) -> None:  # pragma: no cover
        raise AssertionError("mismatched request reached the application")

    app = RequestBodyLimitMiddleware(downstream, max_bytes=16)
    output = _invoke(
        app,
        _scope(headers=[(b"host", b"localhost"), (b"content-length", declared)]),
        [{"type": "http.request", "body": body, "more_body": False}],
    )

    assert _status(output) == 400
    assert _body(output) == {"detail": "Content-Length does not match body"}


def test_host_boundary_accepts_exact_wildcard_ip_and_port() -> None:
    seen: list[str] = []

    async def downstream(scope, _receive, send) -> None:
        seen.append(dict(scope["headers"])[b"host"].decode())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = HostBoundaryMiddleware(
        downstream,
        allowed_hosts=["localhost", "*.example.test", "127.0.0.1", "::1"],
    )
    for host in ("localhost:8787", "reader.example.test", "127.0.0.1", "[::1]:8787"):
        output = _invoke(
            app,
            _scope(headers=[(b"host", host.encode())]),
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        assert _status(output) == 204
    assert seen == ["localhost:8787", "reader.example.test", "127.0.0.1", "[::1]:8787"]


def test_host_boundary_accepts_private_network_ips_without_accepting_dns_names() -> None:
    async def downstream(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = HostBoundaryMiddleware(
        downstream,
        allowed_hosts=Settings().effective_allowed_hosts,
    )
    for host in (
        "127.0.0.1:8788",
        "10.1.2.3:8788",
        "172.16.84.145:8788",
        "192.168.1.9:8788",
        "[fd00::1234]:8788",
    ):
        output = _invoke(
            app,
            _scope(headers=[(b"host", host.encode())]),
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        assert _status(output) == 204

    rejected = _invoke(
        app,
        _scope(headers=[(b"host", b"attacker.example")]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )
    assert _status(rejected) == 400


@pytest.mark.parametrize(
    "host",
    [
        "attacker.test",
        "example.test",
        "",
        "localhost:0",
        "a/b",
        "localhost:" + "9" * 5000,
        "[::1]:" + "9" * 5000,
    ],
)
def test_host_boundary_rejects_untrusted_or_malformed_host(host: str) -> None:
    async def downstream(_scope, _receive, _send) -> None:  # pragma: no cover
        raise AssertionError("untrusted host reached the application")

    app = HostBoundaryMiddleware(downstream, allowed_hosts=["localhost", "*.example.test"])
    output = _invoke(
        app,
        _scope(headers=[(b"host", host.encode())]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )
    assert _status(output) == 400


@pytest.mark.parametrize("host", [b"localhost:\xb2", b"[::1]:\xb2"])
def test_host_boundary_rejects_non_ascii_port_digits(host: bytes) -> None:
    async def downstream(_scope, _receive, _send) -> None:  # pragma: no cover
        raise AssertionError("malformed host reached the application")

    app = HostBoundaryMiddleware(downstream, allowed_hosts=["localhost", "::1"])
    output = _invoke(
        app,
        _scope(headers=[(b"host", host)]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(output) == 400


def test_allow_any_still_requires_one_well_formed_host_header() -> None:
    async def downstream(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = HostBoundaryMiddleware(downstream, allowed_hosts=["*"])
    valid = _invoke(
        app,
        _scope(headers=[(b"host", b"reader.example.test:8787")]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )
    duplicate = _invoke(
        app,
        _scope(headers=[(b"host", b"one.test"), (b"host", b"two.test")]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )
    malformed = _invoke(
        app,
        _scope(headers=[(b"host", b"bad/host")]),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(valid) == 204
    assert _status(duplicate) == 400
    assert _status(malformed) == 400


def test_allowed_host_patterns_are_normalized_and_validated() -> None:
    assert normalize_allowed_hosts(
        ["LOCALHOST.", "*.Example.Test", "[::1]", "192.168.1.42/24", "fd00::1/8"]
    ) == (
        "localhost",
        "*.example.test",
        "::1",
        "192.168.1.0/24",
        "fd00::/8",
    )
    for invalid in (
        "bad*host",
        "example.test:8787",
        "bad/host",
        "bad host",
        "192.168.0.0/99",
        "example.test/24",
    ):
        with pytest.raises(ValueError):
            normalize_allowed_hosts([invalid])
    with pytest.raises(ValueError, match="Wildcard IP"):
        normalize_allowed_hosts(["*.127.0.0.1"])


def test_no_auth_mode_rejects_wildcard_host_boundary() -> None:
    for allowed_hosts in ("*", "*.example.test", "localhost,*.example.test"):
        with pytest.raises(ValueError, match="explicit allowed-host"):
            Settings(auth_mode="none", allowed_hosts=allowed_hosts)


def test_security_headers_harden_api_and_https_responses() -> None:
    async def downstream(_scope, _receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"public")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    app = SecurityHeadersMiddleware(downstream, api_prefix="/api/v1")
    output = _invoke(
        app,
        _scope(
            path="/api/v1/auth/status",
            scheme="https",
            headers=[(b"host", b"localhost")],
        ),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )
    response = next(message for message in output if message["type"] == "http.response.start")
    headers = dict(response["headers"])

    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"content-security-policy"].startswith(b"default-src 'self'")
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"


def test_production_app_installs_all_boundary_middlewares() -> None:
    from backend.app.main import app

    middleware = {item.cls: item.kwargs for item in app.user_middleware}
    assert middleware[RequestBodyLimitMiddleware]["max_bytes"] > 0
    assert middleware[RequestBodyLimitMiddleware]["timeout_seconds"] > 0
    assert middleware[HostBoundaryMiddleware]["allowed_hosts"]
    assert middleware[SecurityHeadersMiddleware]["api_prefix"] == "/api/v1"
    assert not any(
        getattr(route, "name", None)
        in {"openapi", "swagger_ui_html", "swagger_ui_redirect", "redoc_html"}
        for route in app.routes
    )
