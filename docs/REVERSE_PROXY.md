# HTTPS reverse proxy

Keep Affogato RSS Reader bound to loopback when a reverse proxy runs on the Docker host.
The proxy must preserve the original `Host` header and send
`X-Forwarded-Proto: https`.

## Application settings

Set these values in `.env`:

```dotenv
AFFOGATO_RSS_READER_BIND_ADDRESS=127.0.0.1
AFFOGATO_RSS_READER_ALLOWED_HOSTS=reader.example.com
AFFOGATO_RSS_READER_COOKIE_SECURE=true
AFFOGATO_RSS_READER_FORWARDED_ALLOW_IPS=172.18.0.0/16
```

Replace `reader.example.com` with every hostname or IP clients use in the URL;
comma-separate multiple values. Do not use a wildcard on an Internet-facing
deployment.

For direct LAN access without a reverse proxy, set the bind address to
`0.0.0.0`. Private, link-local, and loopback IP literals are accepted by the
default Host boundary, so clients can open the machine's LAN IP without a
machine-specific allowlist. DNS names—including mDNS and local computer
names—must still be added explicitly to `AFFOGATO_RSS_READER_ALLOWED_HOSTS`.

Replace the example CIDR with the subnet or exact address from which the proxy
connects to the container. For the default Compose network, inspect it with:

```console
docker network inspect affogato-rss-reader_default
```

Trust the narrowest practical address or CIDR. Do not use `*` unless the
container network is isolated and every peer is trusted. The default
`127.0.0.1` deliberately ignores forwarded headers from other containers.

The application bounds and times out request bodies after Uvicorn has parsed
the request headers. For an Internet-facing deployment, also configure the
reverse proxy with a short request-header deadline, a body-read deadline of at
most 30 seconds, a 3 MiB request-size limit, and a finite per-client connection
limit. This closes slow-header connections before they consume application
server capacity; exact directive names depend on the proxy version.

After changing `.env`, recreate the service:

```console
docker compose up -d
```

## Caddy on the Docker host

```caddyfile
reader.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Caddy supplies the forwarded scheme and preserves the request host by default.
Keep port 8787 bound to `127.0.0.1`; expose only Caddy's HTTPS port.

## Nginx

```nginx
location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Verification

Open the HTTPS URL, sign in, and perform a write operation such as starring an
entry. A `403 Untrusted request origin` response means the proxy source is not
included in `AFFOGATO_RSS_READER_FORWARDED_ALLOW_IPS`, or the forwarded scheme/host does
not match the browser's Origin.

Confirm the session cookie is marked `Secure` in the browser. Never terminate
TLS at the proxy while leaving `AFFOGATO_RSS_READER_COOKIE_SECURE=false`.
