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
- Auto-tagging and brief generation also send source titles and summaries to
  the owner-selected LLM provider. Auto-tagging continues in the background
  while enabled, and brief schedules repeat that transfer at their configured
  times. Keep these features disabled for content that must not leave the
  instance.
- The optional AI theme generator sends the primary domain, selected domain
  names, and the free-form style preference to the endpoint chosen on that
  screen. Its API key is used for that one request and is not persisted.
- Feed discovery and synchronization reject loopback, private, link-local, and
  other non-public targets by default, including redirect targets. A deployment
  that intentionally subscribes to trusted LAN feeds can opt in with
  `AFFOGATO_RSS_READER_FEED_ALLOW_PRIVATE_NETWORKS=true`; do not enable this on
  an instance where untrusted users can manage subscriptions.
- To enforce that boundary without a DNS-rebinding gap, the application resolves
  feed hostnames locally and connects the selected direct or proxy route to the
  validated IP while retaining the original TLS hostname. A feed proxy therefore
  does not provide DNS-query anonymity; use a trusted local resolver when DNS
  metadata is sensitive.
- The release Compose bundle's update helper mounts the Docker daemon socket,
  which is inherently host-privileged. It is isolated behind the opt-in
  `release-updates` profile and is not started by the default Compose command.
  It has no published port, drops Linux capabilities, and uses a read-only root
  filesystem. Automatic installation is fail-closed until independently signed
  release manifests provide a trust root; checks and verified asset downloads
  continue, and releases must be installed manually. The Web application never
  mounts the socket.

## Release integrity

- Release tags must point to `main` and pass the reusable CI workflow before an
  image or release bundle is published.
- GitHub Actions and Docker base images are pinned to immutable revisions.
  Dependabot opens reviewable updates for both sets of pins.
- A release builds each `amd64` and `arm64` image once under an untagged digest,
  then scans and smoke-tests those exact digests before publishing the combined
  version and `latest` tags. Unreviewed High or Critical Grype findings block
  publication.
- `.grype.yaml` contains only exact, documented CPython CPE exceptions for code
  paths that the service does not use. Each exception is also restricted to the
  current Python version, so a runtime update requires a fresh review.
