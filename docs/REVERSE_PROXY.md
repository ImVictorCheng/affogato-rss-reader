# HTTPS reverse proxy

Keep Affogato RSS Reader bound to loopback when a reverse proxy runs on the Docker host.
The proxy must preserve the original `Host` header and send
`X-Forwarded-Proto: https`.

## Application settings

Set these values in `.env`:

```dotenv
AFFOGATO_RSS_READER_BIND_ADDRESS=127.0.0.1
AFFOGATO_RSS_READER_COOKIE_SECURE=true
AFFOGATO_RSS_READER_FORWARDED_ALLOW_IPS=172.18.0.0/16
```

Replace the example CIDR with the subnet or exact address from which the proxy
connects to the container. For the default Compose network, inspect it with:

```console
docker network inspect affogato-rss-reader_default
```

Trust the narrowest practical address or CIDR. Do not use `*` unless the
container network is isolated and every peer is trusted. The default
`127.0.0.1` deliberately ignores forwarded headers from other containers.

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
