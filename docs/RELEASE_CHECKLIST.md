# Release checklist

Current candidate: `0.3.0`
Database migration head: `0011`

## Repository preparation

- [x] Keep `VERSION`, backend metadata, frontend metadata, container defaults,
  Compose files, and user-facing version labels synchronized.
- [x] Add the dated `0.3.0` section to `CHANGELOG.md`.
- [x] Update the empty-database container smoke test to require migration `0011`.
- [x] Include the new migration in the backend package.
- [x] Keep the release bundle pointed at the versioned GHCR image.

Run the local release checks from the repository root:

```console
python scripts/check_version.py
python scripts/check_utf8.py
python -m pytest backend/tests
cd web
npm run check:ui
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Then build the same development image shape used by Compose and run an
empty-library health and migration smoke test.

## `0.3.0` candidate verification

Verified locally on 2026-08-01:

- [x] Version consistency, UTF-8 validation, diff whitespace, UI conventions,
  and TypeScript type checking
- [x] Backend test suite: 98 passed on Python 3.14, with CI covering both the
  supported Python 3.12 floor and the Python 3.14 container runtime
- [x] Frontend test suite: 56 passed
- [x] Browser end-to-end suite: 4 passed
- [x] Production frontend build
- [x] Compose release bundle dry run, including every document linked from the
  bundled README and a SHA-256 checksum
- [x] Clean hardened `amd64` image rebuild from digest-verified Node and Python
  base contents, including fresh npm/PyPI dependency downloads; CI repeats the
  build, scan, and smoke test for both `amd64` and `arm64`
- [x] Isolated empty-library runtime smoke test: healthy, zero feeds, migration
  `0011`, no foreign-key violations, non-root UID 10001, `lxml` parser, frontend,
  license, and initialized log-volume writes
- [x] Duplicate-entry merge regression and maintenance phase isolation, including
  a backup attempt when synchronization fails
- [x] Published-image MIT license text and non-root call-log write smoke test
- [x] Trusted HTTPS proxy Origin/CSRF write smoke test
- [x] Release Compose passes documented retry and system-proxy variables,
  initializes the host log directory, and bundles the reverse-proxy guide
- [x] Production and test Python locks pass `pip-audit`; frontend dependencies
  pass `npm audit`; Bandit reports no Medium/High findings
- [x] The final image passes Grype's High/Critical gate. Three CPython CPE
  findings are narrowly suppressed for the exact pinned runtime because the two
  affected `tarfile` paths are absent and untrusted HTML is regression-tested on
  the `lxml` backend; all other findings remain gate-enforced
- [x] GitHub Actions and container bases use immutable revisions, release tags
  cannot bypass the complete CI workflow, and the GHCR name is normalized once
  for build metadata, attestation, and the Compose bundle

## Publishing

1. Push the release-preparation commit and wait for every CI job to pass.
2. Create and push the annotated tag `v0.3.0`.
3. Confirm that the Release workflow publishes the `amd64` and `arm64` image,
   provenance attestation, SBOM, Compose archive, and `SHA256SUMS`.
4. Download the archive from the GitHub Release and verify its checksum before
   announcing the release.

Do not create or push the release tag until CI is green. Existing installations
should follow `docs/BACKUP_AND_RESTORE.md` and create a database backup before
pulling the new image.
