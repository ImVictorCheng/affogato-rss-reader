# AGENTS.md

Agent and maintainer instructions for Affogato RSS Reader.

## Release workflow

Releases use `dev` as the working branch and publish from `main`. The Release
workflow (`.github/workflows/release.yml`) validates that the tag equals
`VERSION` and that the tagged commit is contained in `main`, then reruns the
full CI as a quality gate before building and publishing images.

1. **Bump the version on `dev`.** Update `VERSION` and every location that
   `scripts/check_version.py` verifies: `web/package.json`,
   `web/package-lock.json`, `backend/pyproject.toml`,
   `backend/app/config.py`, `web/src/brand.ts`, `compose.yaml`,
   `compose.dev.yaml`, `Dockerfile`, `README.md`, `backend/README.md`, and
   `CHANGELOG.md`. Move the `[Unreleased]` content into a dated
   `## [X.Y.Z]` section, then confirm consistency:

   ```console
   python scripts/check_version.py
   ```

2. **Commit and run the local preflight on `dev`.** A formal release requires
   a clean worktree and no skip flags:

   ```console
   git add -A
   git commit -m "release: prepare X.Y.Z"
   ```

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/release_preflight.ps1
   ```

   The preflight mirrors CI: static checks, the Python 3.12/3.14 backend
   matrix, the web suite and Playwright E2E, amd64/arm64 image builds, the
   Grype High/Critical gate, container smoke tests, release bundle
   generation, and the source SBOM. It requires Docker Desktop with Linux
   container mode and network access.

3. **Squash onto `main` with a non-personal identity.** Never use a personal
   name or email for `main` commits:

   ```console
   git checkout main
   git merge --squash dev
   git -c user.name=deepseek -c user.email=20416460+ImVictorCheng@users.noreply.github.com commit -m "release: prepare X.Y.Z"
   ```

4. **Push `main` and tag.** Pushing `main` triggers CI. Then create and push
   the annotated tag that matches `VERSION` to trigger the Release workflow:

   ```console
   git push origin main
   git -c user.name=deepseek -c user.email=20416460+ImVictorCheng@users.noreply.github.com tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

   Do **not** push `dev`. If CI or Release fails after the tag was pushed,
   fix on `dev`, squash to `main` again, push `main`, and force-move the tag:

   ```console
   git -c user.name=deepseek -c user.email=20416460+ImVictorCheng@users.noreply.github.com tag -f -a vX.Y.Z -m "vX.Y.Z"
   git push --force origin vX.Y.Z
   ```

5. **Merge `main` back into `dev`** to keep the branches aligned. `dev` stays
   local-only unless a push is explicitly requested:

   ```console
   git checkout dev
   git merge main --no-edit
   ```

## Verify a release

```console
gh release view vX.Y.Z
```

Expected assets: `affogato-rss-reader-X.Y.Z.tar.gz`,
`affogato-rss-reader-compose-X.Y.Z.yaml`,
`affogato-rss-reader-source.spdx.json`, and `SHA256SUMS`.

## Notes

- All repository text files must be UTF-8 without a BOM; run
  `python scripts/check_utf8.py` before committing.
- The local preflight writes bundles under the ignored `.local-backups/`
  directory and must never replace artifacts produced by GitHub Actions.
