from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from threading import local
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")
NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")
SENSITIVE_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
}
CONDITIONAL_REQUEST_HEADERS = {
    "if-match",
    "if-modified-since",
    "if-none-match",
    "if-range",
    "if-unmodified-since",
    "range",
}


class UnsafeRemoteURL(ValueError):
    """Raised when an outbound URL could reach a non-public network."""


class RemoteResponseTooLarge(ValueError):
    """Raised when a remote response exceeds its configured byte limit."""


class UnsafeRemoteResponse(ValueError):
    """Raised when remote response framing cannot be consumed safely."""


@dataclass(frozen=True)
class RemoteDocument:
    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Python's ``is_global`` deliberately includes multicast addresses (and,
    # on some versions, deprecated IPv6 site-local addresses).  Neither is a
    # safe outbound target for untrusted feed URLs: both can address the local
    # network even though the standard-library predicate says "global".
    if address.is_multicast or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_public_address(address.ipv4_mapped)
    if isinstance(address, ipaddress.IPv6Address):
        # The well-known NAT64 /96 is marked reserved by some Python versions,
        # so validate its embedded IPv4 address before the generic reserved
        # check.  The local-use /48 has multiple RFC 6052 embedding layouts;
        # fail closed rather than guessing which 32 bits identify the target.
        if address in NAT64_WELL_KNOWN:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            return _is_public_address(embedded)
        if address in NAT64_LOCAL_USE:
            return False
        if (
            address.is_site_local
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            return False
        if address in IPV4_COMPATIBLE:
            return False
        if address.sixtofour is not None:
            return address.is_global and _is_public_address(address.sixtofour)
        if address.teredo is not None:
            server, client = address.teredo
            return (
                address.is_global
                and _is_public_address(server)
                and _is_public_address(client)
            )
    return address.is_global


class _SNIOverrideStream:
    def __init__(self, stream, backend: "_SNIOverrideBackend") -> None:
        self._stream = stream
        self._backend = backend

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, self._backend.bounded_timeout(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, self._backend.bounded_timeout(timeout))

    def close(self) -> None:
        self._stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        effective_hostname = self._backend.hostname_for(server_hostname)
        secured = self._stream.start_tls(
            ssl_context,
            server_hostname=effective_hostname,
            timeout=self._backend.bounded_timeout(timeout),
        )
        return _SNIOverrideStream(secured, self._backend)

    def get_extra_info(self, info: str):
        return self._stream.get_extra_info(info)


class _SNIOverrideBackend:
    """Preserve the original TLS name when a proxy tunnels to a pinned IP."""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._state = local()

    def _overrides(self) -> dict[str, str]:
        overrides = getattr(self._state, "overrides", None)
        if overrides is None:
            overrides = {}
            self._state.overrides = overrides
        return overrides

    def add(self, address: str, hostname: str) -> None:
        self._overrides()[address] = hostname

    def remove(self, address: str, hostname: str) -> None:
        overrides = self._overrides()
        if overrides.get(address) == hostname:
            overrides.pop(address, None)

    def hostname_for(self, server_hostname: str | None) -> str | None:
        return self._overrides().get(server_hostname, server_hostname)

    def set_deadline(self, deadline: float | None) -> None:
        self._state.deadline = deadline

    def clear_deadline(self) -> None:
        self._state.deadline = None

    def bounded_timeout(self, timeout: float | None) -> float | None:
        deadline = getattr(self._state, "deadline", None)
        if deadline is None:
            return timeout
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise httpx.TimeoutException("Feed fetch exceeded its total time limit")
        return remaining if timeout is None else min(timeout, remaining)

    def connect_tcp(self, **kwargs):
        kwargs["timeout"] = self.bounded_timeout(kwargs.get("timeout"))
        stream = self._backend.connect_tcp(**kwargs)
        return _SNIOverrideStream(stream, self)

    def connect_unix_socket(self, **kwargs):
        kwargs["timeout"] = self.bounded_timeout(kwargs.get("timeout"))
        stream = self._backend.connect_unix_socket(**kwargs)
        return _SNIOverrideStream(stream, self)

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


def _install_sni_override(
    transport,
    pinned_url: httpx.URL,
    hostname: str,
    deadline: float | None,
) -> _SNIOverrideBackend | None:
    """Install a fail-closed httpcore adapter for direct and proxied TLS."""

    if isinstance(transport, httpx.MockTransport):
        return None
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is None or backend is None:
        raise UnsafeRemoteResponse(
            "The configured HTTP transport cannot enforce feed DNS pinning"
        )
    proxy_url = getattr(pool, "_proxy_url", None)
    if (
        proxy_url is not None
        and proxy_url.scheme == b"https"
        and proxy_url.host.decode("ascii").removesuffix(".") == pinned_url.host
    ):
        raise UnsafeRemoteResponse(
            "An HTTPS proxy must not use the same IP literal as the pinned feed target"
        )
    if not isinstance(backend, _SNIOverrideBackend):
        backend = _SNIOverrideBackend(backend)
        pool._network_backend = backend  # noqa: SLF001
    backend.add(pinned_url.host, hostname)
    backend.set_deadline(deadline)
    return backend


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    try:
        hostname = httpx.URL(url).raw_host.decode("ascii").removesuffix(".")
    except (UnicodeDecodeError, httpx.InvalidURL):
        hostname = (parsed.hostname or "").removesuffix(".").lower()
    return (
        scheme,
        hostname,
        parsed.port or (443 if scheme == "https" else 80),
    )


def _resolved_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname.removesuffix("."))
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            raise UnsafeRemoteURL(f"Unable to resolve feed host: {hostname}") from exc
        addresses = tuple(dict.fromkeys(str(record[4][0]).split("%", 1)[0] for record in records))
        if not addresses:
            raise UnsafeRemoteURL(f"Unable to resolve feed host: {hostname}")
        return addresses
    return (str(literal),)


def _validated_target(
    url: str,
    *,
    allow_private_networks: bool,
) -> tuple[httpx.URL, str, bool]:
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise UnsafeRemoteURL("Invalid HTTP(S) feed URL") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeRemoteURL("Only absolute HTTP(S) feed URLs are supported")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeRemoteURL("Feed URLs must not contain credentials")

    original_url = httpx.URL(url)
    hostname = original_url.raw_host.decode("ascii").removesuffix(".")
    addresses = _resolved_addresses(hostname, port)
    parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    public_results = tuple(_is_public_address(address) for address in parsed_addresses)
    if any(public_results) and not all(public_results):
        raise UnsafeRemoteURL(
            f"Feed host has mixed public and non-public DNS results: {hostname}"
        )
    target_is_private = not all(public_results)
    if not allow_private_networks and target_is_private:
        raise UnsafeRemoteURL(f"Feed host resolves to a non-public address: {hostname}")

    # Connect to the already validated address. The original Host header and TLS
    # SNI are restored by ``fetch_remote_document`` below. This closes the DNS
    # validation/connection race that a rebinding response could otherwise exploit.
    return (
        original_url.copy_with(host=str(parsed_addresses[0])),
        hostname,
        target_is_private,
    )


def _host_header(url: str, hostname: str) -> str:
    parsed = urlsplit(url)
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None and not (
        (parsed.scheme.lower() == "http" and parsed.port == 80)
        or (parsed.scheme.lower() == "https" and parsed.port == 443)
    ):
        host = f"{host}:{parsed.port}"
    return host


def fetch_remote_document(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int,
    max_redirects: int,
    allow_private_networks: bool = False,
    total_timeout_seconds: float | None = None,
) -> RemoteDocument:
    """Fetch a bounded document while validating and pinning every redirect target."""

    current_url = url
    previous_scheme: str | None = None
    forward_conditionals = True
    initial_target_private: bool | None = None
    deadline = (
        monotonic() + total_timeout_seconds
        if total_timeout_seconds is not None
        else None
    )
    for redirect_count in range(max_redirects + 1):
        if deadline is not None and monotonic() >= deadline:
            raise httpx.TimeoutException("Feed fetch exceeded its total time limit")
        pinned_url, hostname, target_is_private = _validated_target(
            current_url,
            allow_private_networks=allow_private_networks,
        )
        if initial_target_private is None:
            initial_target_private = target_is_private
        elif target_is_private and not initial_target_private:
            raise UnsafeRemoteURL(
                "A public feed must not redirect to a non-public address"
            )

        scheme = urlsplit(current_url).scheme.lower()
        if previous_scheme == "https" and scheme != "https":
            raise UnsafeRemoteURL("HTTPS feed redirects must not downgrade to HTTP")

        request = client.build_request("GET", pinned_url, headers=headers)
        for header in SENSITIVE_REQUEST_HEADERS:
            request.headers.pop(header, None)
        if not forward_conditionals:
            for header in CONDITIONAL_REQUEST_HEADERS:
                request.headers.pop(header, None)
        # Select the route from the original hostname before replacing it with
        # its validated IP. This preserves HTTPX's NO_PROXY behavior.
        transport_url = httpx.URL(current_url)
        transport = client._transport_for_url(transport_url)  # noqa: SLF001
        override_backend = _install_sni_override(
            transport,
            pinned_url,
            hostname,
            deadline,
        )
        request.headers["host"] = _host_header(current_url, hostname)
        request.extensions["sni_hostname"] = hostname
        # The connection pool is keyed by the pinned IP. Closing after each
        # request prevents a cross-host redirect to the same IP from reusing a
        # TLS session authenticated for a different hostname.
        request.headers["connection"] = "close"
        # Reject compressed bodies rather than letting a tiny hostile gzip chunk
        # expand without a bound inside HTTPX's decoder.
        request.headers["accept-encoding"] = "identity"
        try:
            response = transport.handle_request(request)
        except Exception:
            if override_backend is not None:
                override_backend.remove(pinned_url.host, hostname)
                override_backend.clear_deadline()
            raise
        response.request = request
        try:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                    return RemoteDocument(
                        url=current_url,
                        status_code=response.status_code,
                        headers=response.headers,
                        content=b"",
                    )
                if redirect_count >= max_redirects:
                    raise UnsafeRemoteURL("Feed redirect limit exceeded")
                previous_scheme = scheme
                next_url = urljoin(current_url, location)
                if _origin(next_url) != _origin(current_url):
                    forward_conditionals = False
                current_url = next_url
                continue

            if response.status_code == 304:
                return RemoteDocument(
                    url=current_url,
                    status_code=response.status_code,
                    headers=response.headers,
                    content=b"",
                )
            response.raise_for_status()

            content_encoding = response.headers.get("content-encoding", "identity")
            if content_encoding.strip().lower() not in {"", "identity"}:
                raise UnsafeRemoteResponse(
                    "Encoded feed responses are not accepted; the server must honor Accept-Encoding: identity"
                )

            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    length = int(declared_length)
                except ValueError as exc:
                    raise RemoteResponseTooLarge("Remote response has an invalid Content-Length") from exc
                if length < 0 or length > max_bytes:
                    raise RemoteResponseTooLarge(
                        f"Remote response exceeds the {max_bytes}-byte limit"
                    )

            chunks: list[bytes] = []
            received = 0
            stream = (
                (response.content,)
                if response.is_stream_consumed
                else response.iter_raw()
            )
            for chunk in stream:
                if deadline is not None and monotonic() >= deadline:
                    raise httpx.TimeoutException(
                        "Feed fetch exceeded its total time limit"
                    )
                received += len(chunk)
                if received > max_bytes:
                    raise RemoteResponseTooLarge(
                        f"Remote response exceeds the {max_bytes}-byte limit"
                    )
                chunks.append(chunk)
            return RemoteDocument(
                url=current_url,
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
            )
        finally:
            response.close()
            if override_backend is not None:
                override_backend.remove(pinned_url.host, hostname)
                override_backend.clear_deadline()

    raise UnsafeRemoteURL("Feed redirect limit exceeded")  # pragma: no cover
