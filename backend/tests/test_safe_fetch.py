from __future__ import annotations

import gzip
import ipaddress

import httpx
import pytest

from backend.app.safe_fetch import (
    _SNIOverrideBackend,
    RemoteResponseTooLarge,
    UnsafeRemoteURL,
    UnsafeRemoteResponse,
    fetch_remote_document,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    def resolve(hostname: str, _port: int) -> tuple[str, ...]:
        try:
            return (str(ipaddress.ip_address(hostname)),)
        except ValueError:
            return ("93.184.216.34",)

    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        resolve,
    )


def test_fetch_pins_validated_ip_but_preserves_host_and_tls_name(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda hostname, _port: ("93.184.216.34",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "feeds.example:8443"
        assert request.extensions["sni_hostname"] == "feeds.example"
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["connection"] == "close"
        return httpx.Response(200, content=b"feed", request=request)

    with _client(handler) as client:
        document = fetch_remote_document(
            client,
            "https://feeds.example:8443/rss?q=1",
            max_bytes=1024,
            max_redirects=2,
        )

    assert document.url == "https://feeds.example:8443/rss?q=1"
    assert document.content == b"feed"


def test_fetch_uses_idna_hostname_for_dns_host_and_sni(monkeypatch):
    resolved: list[str] = []

    def resolve(hostname, _port):
        resolved.append(hostname)
        return ("93.184.216.34",)

    monkeypatch.setattr("backend.app.safe_fetch._resolved_addresses", resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["host"] == "xn--fsqu00a.xn--0zwm56d"
        assert request.extensions["sni_hostname"] == "xn--fsqu00a.xn--0zwm56d"
        return httpx.Response(200, content=b"feed", request=request)

    with _client(handler) as client:
        fetch_remote_document(
            client,
            "https://例子.测试/rss",
            max_bytes=1024,
            max_redirects=0,
        )

    assert resolved == ["xn--fsqu00a.xn--0zwm56d"]


def test_fetch_selects_transport_before_replacing_no_proxy_hostname(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda _hostname, _port: ("93.184.216.34",),
    )
    selected: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        return httpx.Response(200, content=b"feed", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        def select(url: httpx.URL):
            selected.append(url.host)
            return transport

        monkeypatch.setattr(client, "_transport_for_url", select)
        fetch_remote_document(
            client,
            "https://feeds.example/rss",
            max_bytes=1024,
            max_redirects=0,
        )

    assert selected == ["feeds.example"]


def test_httpx_no_proxy_route_differs_after_ip_rewrite(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "feeds.example")
    with httpx.Client(trust_env=True) as client:
        original = client._transport_for_url(httpx.URL("https://feeds.example/rss"))
        pinned = client._transport_for_url(httpx.URL("https://93.184.216.34/rss"))

    assert original is not pinned


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "file:///etc/passwd",
        "https://user:secret@example.com/feed",
    ],
)
def test_fetch_rejects_private_non_http_and_credentialed_targets(url):
    with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(UnsafeRemoteURL):
            fetch_remote_document(client, url, max_bytes=1024, max_redirects=1)


@pytest.mark.parametrize(
    "url",
    [
        "http://[::127.0.0.1]/",
        "http://[64:ff9b::127.0.0.1]/",
        "http://[64:ff9b:1::127.0.0.1]/",
    ],
)
def test_fetch_rejects_ipv4_translation_addresses_for_private_targets(url):
    with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(UnsafeRemoteURL, match="non-public"):
            fetch_remote_document(client, url, max_bytes=1024, max_redirects=1)


def test_fetch_allows_well_known_nat64_mapping_of_public_ipv4():
    with _client(
        lambda request: httpx.Response(200, content=b"feed", request=request)
    ) as client:
        document = fetch_remote_document(
            client,
            "https://[64:ff9b::8.8.8.8]/rss",
            max_bytes=1024,
            max_redirects=0,
        )

    assert document.content == b"feed"


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://239.255.255.250/",
        "http://[::]/",
        "http://[ff02::1]/",
        "http://[ff05::1]/",
        "http://[fec0::1]/",
    ],
)
def test_fetch_rejects_unspecified_multicast_and_site_local_targets(url):
    with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(UnsafeRemoteURL, match="non-public"):
            fetch_remote_document(client, url, max_bytes=1024, max_redirects=1)


def test_fetch_rejects_dns_answers_mixed_with_private_address(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda hostname, _port: ("93.184.216.34", "10.0.0.8"),
    )
    with _client(lambda request: httpx.Response(200, request=request)) as client:
        with pytest.raises(UnsafeRemoteURL, match="non-public"):
            fetch_remote_document(
                client,
                "https://feeds.example/rss",
                max_bytes=1024,
                max_redirects=1,
            )


def test_fetch_revalidates_redirect_targets(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda hostname, _port: (
            ("127.0.0.1",) if hostname == "127.0.0.1" else ("93.184.216.34",)
        ),
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/internal"},
            request=request,
        )

    with _client(handler) as client:
        with pytest.raises(UnsafeRemoteURL, match="non-public"):
            fetch_remote_document(
                client,
                "https://feeds.example/rss",
                max_bytes=1024,
                max_redirects=3,
            )
    assert calls == 1


def test_public_feed_cannot_redirect_private_even_with_lan_opt_in(monkeypatch):
    def resolve(hostname, _port):
        return (
            ("127.0.0.1",)
            if hostname == "127.0.0.1"
            else ("93.184.216.34",)
        )

    monkeypatch.setattr("backend.app.safe_fetch._resolved_addresses", resolve)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "https://127.0.0.1/latest/meta-data"},
            request=request,
        )

    with _client(handler) as client:
        with pytest.raises(UnsafeRemoteURL, match="public feed"):
            fetch_remote_document(
                client,
                "https://feeds.example/rss",
                max_bytes=1024,
                max_redirects=2,
                allow_private_networks=True,
            )
    assert calls == 1


def test_cross_origin_redirect_strips_conditionals_and_client_credentials():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            assert request.headers["if-none-match"] == '"tracking-id"'
            assert "authorization" not in request.headers
            return httpx.Response(
                302,
                headers={"location": "https://other.test/feed"},
                request=request,
            )
        assert "if-none-match" not in request.headers
        assert "if-modified-since" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"feed", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer should-not-leak"},
    ) as client:
        document = fetch_remote_document(
            client,
            "https://origin.test/feed",
            headers={
                "If-None-Match": '"tracking-id"',
                "If-Modified-Since": "Mon, 10 Aug 2026 00:00:00 GMT",
            },
            max_bytes=1024,
            max_redirects=2,
        )

    assert document.url == "https://other.test/feed"
    assert len(calls) == 2


def test_fetch_rejects_https_downgrade(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda hostname, _port: ("93.184.216.34",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://feeds.example/insecure"},
            request=request,
        )

    with _client(handler) as client:
        with pytest.raises(UnsafeRemoteURL, match="downgrade"):
            fetch_remote_document(
                client,
                "https://feeds.example/rss",
                max_bytes=1024,
                max_redirects=3,
            )


def test_fetch_limits_declared_and_streamed_response_size():
    for headers, content in [
        ({"content-length": "2048"}, b""),
        ({}, b"x" * 2048),
    ]:
        with _client(
            lambda request, h=headers, c=content: httpx.Response(
                200,
                headers=h,
                content=c,
                request=request,
            )
        ) as client:
            with pytest.raises(RemoteResponseTooLarge):
                fetch_remote_document(
                    client,
                    "https://example.test/feed",
                    max_bytes=1024,
                    max_redirects=1,
                )


def test_fetch_rejects_encoded_content_before_decompression():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(b"encoded")),
            request=request,
        )

    with _client(handler) as client:
        with pytest.raises(UnsafeRemoteResponse, match="Encoded feed responses"):
            fetch_remote_document(
                client,
                "https://example.test/feed",
                max_bytes=1024,
                max_redirects=1,
            )


def test_fetch_limits_redirect_chain():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"}, request=request)

    with _client(handler) as client:
        with pytest.raises(UnsafeRemoteURL, match="redirect limit"):
            fetch_remote_document(
                client,
                "https://example.test/feed",
                max_bytes=1024,
                max_redirects=1,
            )


def test_fetch_enforces_total_deadline_while_streaming(monkeypatch):
    times = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr("backend.app.safe_fetch.monotonic", lambda: next(times))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"slow"),
            request=request,
        )

    with _client(handler) as client:
        with pytest.raises(httpx.TimeoutException, match="total time"):
            fetch_remote_document(
                client,
                "https://example.test/feed",
                max_bytes=1024,
                max_redirects=0,
                total_timeout_seconds=60,
            )


def test_private_networks_require_explicit_opt_in():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, content=b"local", request=request)

    with _client(handler) as client:
        document = fetch_remote_document(
            client,
            "http://127.0.0.1/feed",
            max_bytes=1024,
            max_redirects=0,
            allow_private_networks=True,
        )
    assert document.content == b"local"


def test_sni_override_backend_preserves_hostname_inside_proxy_tunnel():
    calls: list[str | None] = []

    class FakeStream:
        def read(self, _max_bytes, _timeout=None):
            return b""

        def write(self, _buffer, _timeout=None):
            return None

        def close(self):
            return None

        def start_tls(self, _ssl_context, server_hostname=None, timeout=None):
            calls.append(server_hostname)
            return self

        def get_extra_info(self, _info):
            return None

    class FakeBackend:
        def connect_tcp(self, **_kwargs):
            return FakeStream()

        def connect_unix_socket(self, **_kwargs):
            return FakeStream()

        def sleep(self, _seconds):
            return None

    backend = _SNIOverrideBackend(FakeBackend())
    backend.add("93.184.216.34", "feeds.example")
    stream = backend.connect_tcp(host="proxy.test", port=8080)
    stream = stream.start_tls(None, server_hostname="proxy.test")
    stream.start_tls(None, server_hostname="93.184.216.34")

    assert calls == ["proxy.test", "feeds.example"]


def test_https_proxy_ip_must_not_collide_with_pinned_target(monkeypatch):
    monkeypatch.setattr(
        "backend.app.safe_fetch._resolved_addresses",
        lambda _hostname, _port: ("93.184.216.34",),
    )
    with httpx.Client(
        proxy="https://93.184.216.34:8443",
        trust_env=False,
    ) as client:
        with pytest.raises(UnsafeRemoteResponse, match="same IP literal"):
            fetch_remote_document(
                client,
                "https://feeds.example/rss",
                max_bytes=1024,
                max_redirects=0,
            )
