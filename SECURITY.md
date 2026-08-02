# Security policy

## Supported versions

Only the latest released minor version receives security fixes.

## Reporting

Do not open a public issue for a suspected vulnerability. Use the repository's
private security advisory feature and include reproduction steps, affected
versions, and impact. Maintainers should acknowledge a report within seven days.

## Deployment guidance

- Keep the default loopback binding until the owner password is created.
- Put the service behind HTTPS when traffic crosses an untrusted network.
  Follow `docs/REVERSE_PROXY.md`, trust only the proxy's address or network,
  and set `AFFOGATO_RSS_READER_COOKIE_SECURE=true`.
- Use `AFFOGATO_RSS_READER_AUTH_MODE=none` only on a trusted, access-controlled network.
- Keep the data and secrets volumes private. API keys saved in Settings are
  encrypted in the database; the independent secrets volume contains the
  master key needed to decrypt them.
- Back up the secrets volume separately. Anyone who obtains both the database
  and its master key can decrypt saved keys, while losing the master key makes
  those keys unrecoverable.
- Translation is disabled by default. Enabling it sends source titles and
  summaries to the selected third-party provider. In automatic fallback mode,
  Google GTX also receives the text if the primary provider fails; manual mode
  stops without sending it to GTX.
- The release Compose bundle's update helper mounts the Docker daemon socket,
  which is inherently host-privileged. It has no published port, drops Linux
  capabilities, uses a read-only root filesystem, and accepts
  only a fixed repository plus a digest-verified, tightly validated Compose
  asset. The Web application never mounts the socket. If this trust model is not
  acceptable for a deployment, stop the `updater` service and install releases
  manually; the application will continue to check and download updates.

## Release integrity

- Release tags must point to `main` and pass the reusable CI workflow before an
  image or release bundle is published.
- GitHub Actions and Docker base images are pinned to immutable revisions.
  Dependabot opens reviewable updates for both sets of pins.
- CI builds, runs, and scans both `amd64` and `arm64` images. Unreviewed High or
  Critical Grype findings block the release.
- `.grype.yaml` contains only exact, documented CPython CPE exceptions for code
  paths that the service does not use. Each exception is also restricted to the
  current Python version, so a runtime update requires a fresh review.
