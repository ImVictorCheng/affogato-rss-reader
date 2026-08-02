# Pre-release TODO

This list tracks the unresolved findings from the 2026-08-01 pre-release audit. The
container recreation, GitHub 304 retry, and scheduler starvation findings were fixed
in the working tree and are therefore not listed below.

## P1 — release blockers

- [ ] Eliminate the update Compose TOCTOU window. Validate and install from the same
  immutable snapshot instead of reopening a reader-writable path after validation.
- [ ] Replace the partial Compose validation with a strict schema/allowlist for all
  services and named-volume definitions, including environment, commands, users,
  ports, network modes, and volume driver options.
- [ ] Put the verified image digest directly in every release service image reference
  (`repository:version@sha256:...`), so first installation and manual pulls cannot use
  a mutable tag.
- [ ] Add an explicit Host trust boundary for first-owner setup and no-auth mode, and
  cover DNS-rebinding scenarios in tests.

## P2 — important hardening and reliability

- [ ] Add configurable retention for entries, synchronization history, completed
  jobs/checkpoints, and translation cache. Preserve starred/later entries and
  brief references, expose the unread-entry tradeoff, and reclaim SQLite pages
  after bulk deletion without blocking normal reads.
- [ ] Bound feed download and parsing work: stream with a response-byte ceiling,
  cap entries per response, and validate stored title, summary, content, URL,
  author, and category sizes before they reach SQLite.
- [ ] Add a storage diagnostics and cleanup surface showing database, WAL,
  backups, logs, caches, Docker image guidance, free space, and threshold
  warnings, with explicit and recoverable cleanup actions.

- [x] Override the Web health check for the updater service; its helper process does
  not listen on port 8787.
- [ ] Define a database rollback strategy for failed post-migration updates;
  pre-update backups are now written atomically and integrity-checked, but the
  automatic rollback currently restores only the prior container/Compose state.
- [ ] Update the updater and log-init services as part of the supported update flow,
  or clearly enforce a required manual Compose reconciliation step.
- [ ] Ignore and safely permission `compose.previous.yaml`, and ensure it cannot retain
  interpolated credentials or personal host paths.
- [ ] Make custom global-proxy mode fail closed when the custom proxy is disabled.
- [ ] Restrict entry links to safe HTTP/HTTPS schemes and decide whether remote images
  in generated Markdown must use the application proxy or be disabled by default.
- [ ] Prevent the old visibility observer from marking an article read when switching
  between different feeds that share the same title.
- [ ] Provide a persistent, documented way to disable the Docker-Socket updater across
  future `docker compose up` runs.
- [ ] Add hashes to Python dependency locks, pin isolated build requirements, and make
  the container build fully reproducible.
- [ ] Add CI coverage for the real Docker update lifecycle, rollback, `pip-audit`,
  `npm audit`, Bandit, and a scan of the exact release image.
- [x] Avoid unnecessary anonymous data/secrets volumes in the updater and log-init
  containers by masking the image-declared paths with ephemeral tmpfs mounts.
- [ ] Validate update result schema versions and require request/version correlation
  for every result, including when the current state has no request identifier.

## P3 — follow-up polish

- [ ] Keep the update banner below modal backdrops and make dismiss persist longer
  than the status polling interval.
- [ ] Make update settings text reflect whether automatic checks are disabled.
- [ ] Clear proxy-password drafts when leaving proxy settings and add a retry action
  when settings loading fails.
- [ ] Add defense-in-depth response headers, including CSP/frame restrictions,
  Referrer-Policy, and appropriate no-store headers for sensitive API responses.
- [ ] Add accessible labels to icon-only mobile controls.
- [ ] Validate non-empty release digests, avoid publishing mutable tags before all
  release assets succeed, and include the SBOM in release checksums.
