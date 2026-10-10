# Release checklist

Use this checklist for every release alongside the
[release workflow in AGENTS.md](../AGENTS.md#release-workflow). Prepare on `dev`
and publish from `main`; record the candidate version from `VERSION` and fresh
verification results for each release. The dated results below document the
historical `0.4.0` audit.

## `0.5.1` preparation — 2026-10-10

Candidate version: `0.5.1`. Database migration head: `0016`.
Both READMEs and the release notes have been reviewed against the implementation.
The full local preflight must pass against the committed candidate before publishing;
its source SBOM, image scan reports, and run log are kept under `.local-backups/`.

## Repository preparation

- [ ] Review and update `README.md` and `backend/README.md` against the final
  implementation, including configuration, enabled or dormant workflows,
  user-facing behavior, and relevant API or migration changes. Record the
  review even when the existing documentation remains accurate.
- [ ] Review `CHANGELOG.md`'s `[Unreleased]` section, consolidate duplicate and
  superseded entries, and describe the final shipped behavior. Preserve
  published version sections as historical records.
- [ ] Keep `VERSION`, backend metadata, frontend metadata, container defaults,
  Compose files, and user-facing version labels synchronized.
- [ ] Move the finalized `[Unreleased]` notes into a dated section matching the
  new `VERSION`, leaving `[Unreleased]` empty for the formal release. Review
  that section as the release notes.
- [ ] Keep the container smoke test's expected migration head aligned with the
  packaged Alembic migrations, and include new migrations in the backend package.
- [ ] Keep the release bundle pointed at the versioned GHCR image and verify
  that documents linked from its README are included.

Run the documentation and version checks from the repository root:

```console
python scripts/check_version.py --release
python scripts/check_utf8.py
git diff --check
```

Commit the release preparation on `dev`, then run the complete local preflight
with a clean worktree and no skip flags:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/release_preflight.ps1
```

The preflight covers static checks, the Python 3.12/3.14 backend matrix, the web
suite and Playwright E2E, `amd64`/`arm64` image builds, the Grype High/Critical
gate, container smoke tests, release Compose validation, and the source SBOM.
It requires Docker Desktop in Linux container mode and network access. Follow
`AGENTS.md` for the scanner database seed option and Docker timeout recovery.

## Historical `0.4.0` candidate verification

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

1. After the local preflight passes, squash `dev` onto `main` using the
   non-personal commit identity specified in `AGENTS.md`, push `main`, and wait
   for every CI job to pass. Keep `dev` local unless explicitly asked to push it.
2. Create and push the annotated tag `vX.Y.Z` matching `VERSION`.
3. Confirm that the Release workflow publishes the `amd64` and `arm64` image,
   provenance attestation, `affogato-rss-reader-X.Y.Z.tar.gz`,
   `affogato-rss-reader-compose-v2-X.Y.Z.yaml`,
   `affogato-rss-reader-source.spdx.json`, and `SHA256SUMS`.
4. Download the archive from the GitHub Release and verify its checksum before
   announcing the release.
5. Merge `main` back into `dev` to keep the branches aligned.

Do not create or push the release tag until CI is green. Published versions and
tags are immutable; a failed release that published an image or asset requires
a new patch version. Existing installations should follow
[BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md) and create a database backup before
pulling the new image.
