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
   Grype High/Critical gate, container smoke tests, release Compose structural
   validation, and the source SBOM. It requires Docker Desktop with Linux
   container mode and network access.

   On slow networks, `-GrypeDatabaseArchive <path>` can seed the scanner cache
   from a separately downloaded official Grype database archive. The preflight
   verifies its SHA256 against Anchore's current database metadata before import.
   This option still refreshes the database online and retains hash, age, and
   vulnerability checks; it is not a skip flag.

3. **Squash onto `main` with a non-personal identity.** Never use a personal
   name or email for `main` commits. Use the current LLM model name as
   `user.name` and `ImVictorCheng@users.noreply.github.com` as `user.email`
   (for example, `ChatGPT 5.6 Luna`):

   ```console
   git checkout main
   git merge --squash dev
   git -c user.name="<LLM model name>" -c user.email=ImVictorCheng@users.noreply.github.com commit -m "release: prepare X.Y.Z"
   ```

4. **Push `main` and tag.** Pushing `main` triggers CI. Then create and push
   the annotated tag that matches `VERSION` to trigger the Release workflow:

   ```console
   git push origin main
   git -c user.name="<LLM model name>" -c user.email=ImVictorCheng@users.noreply.github.com tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

   Do **not** push `dev`. Published tags and release versions are immutable. If
   CI or Release fails after a tag has published any image or asset, fix on
   `dev`, prepare the next patch version, squash it to `main`, and create a new
   tag. Never force-move or reuse the failed version tag.

   For example, a published but failed `v1.2.3` must be replaced by a newly
   prepared `v1.2.4`; do not move `v1.2.3`.

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
`affogato-rss-reader-compose-v2-X.Y.Z.yaml`,
`affogato-rss-reader-source.spdx.json`, and `SHA256SUMS`.

## Local development instance

A separate published instance runs outside this repository on port `8787` for
daily use; never touch it. For development, spin up an isolated temporary
instance **always** on port `8788` with its own data:

- Project name and port come from the repo-local `.env` (gitignored):
  `COMPOSE_PROJECT_NAME=affogato-rss-reader-dev` and
  `AFFOGATO_RSS_READER_PORT=8788`. Volumes are scoped by project name, so
  `affogato-rss-reader-dev_*` never collide with the production `affogato-rss-reader_*`.
- Data lives in the gitignored `./data` bind mount; secrets, update-control
  and logs are separate volumes/dirs under the dev project. The dev `updater`
  service is disabled via the `release-updates` profile.
- Rebuild from source (frontend + backend) and (re)start as needed:

  ```console
  docker compose -f compose.yaml -f compose.dev.yaml build
  docker compose -f compose.yaml -f compose.dev.yaml up -d
  ```

  Verify: `docker compose -p affogato-rss-reader-dev ps` and
  `Invoke-WebRequest http://127.0.0.1:8788/api/v1/health`. Clean up when done:

  ```console
  docker compose -p affogato-rss-reader-dev down -v --remove-orphans
  ```

- First login uses `docker compose -p affogato-rss-reader-dev exec reader affogato-rss-reader initial-password`.

## Docker Desktop network-timeout fallback (Windows)

Use this fallback only when a Docker operation that must reach the internet
(`pull`, `build`, a scanner database/image download, or the release preflight)
repeatedly fails with connection or handshake timeouts while the host proxy is
working. Docker Desktop does not expose this setting through its CLI, so the
user-level settings store must be changed while Docker Desktop is stopped.

The normal project state is **Containers proxy: No proxy**, represented by an
empty `ContainersProxyHTTPMode`. Never overwrite a custom non-empty value. If
the backup below already exists, treat it as an interrupted earlier task and
restore it before doing any more Docker work.

Temporarily switch to **Same as host proxy** with PowerShell:

```powershell
$dockerSettingsDir = Join-Path $env:APPDATA "Docker"
$dockerSettingsPath = Join-Path $dockerSettingsDir "settings-store.json"
$dockerProxyBackupPath = Join-Path $dockerSettingsDir "settings-store.json.affogato-temporary-proxy.bak"

if (-not (Test-Path -LiteralPath $dockerSettingsPath -PathType Leaf)) {
    throw "Docker Desktop settings-store.json was not found."
}
if (Test-Path -LiteralPath $dockerProxyBackupPath) {
    throw "A temporary proxy backup already exists; restore it before continuing."
}

docker desktop stop
$dockerProcesses = @(
    Get-Process -Name "Docker Desktop", "com.docker.backend" -ErrorAction SilentlyContinue
)
if ($dockerProcesses.Count -ne 0) {
    throw "Docker Desktop is still running; do not edit its settings store."
}

$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -ne "") {
    throw "Refusing to overwrite a Containers proxy setting other than No proxy."
}

Copy-Item -LiteralPath $dockerSettingsPath -Destination $dockerProxyBackupPath
$dockerSettingsText = [IO.File]::ReadAllText($dockerSettingsPath)
$dockerNoProxyEntry = '"ContainersProxyHTTPMode": ""'
$dockerHostProxyEntry = '"ContainersProxyHTTPMode": "same-as-host-proxy"'
if ([regex]::Matches($dockerSettingsText, [regex]::Escape($dockerNoProxyEntry)).Count -ne 1) {
    throw "Expected exactly one No proxy setting; the backup was kept and no edit was made."
}
[IO.File]::WriteAllText(
    $dockerSettingsPath,
    $dockerSettingsText.Replace($dockerNoProxyEntry, $dockerHostProxyEntry),
    [Text.UTF8Encoding]::new($false)
)

$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -ne "same-as-host-proxy") {
    throw "The temporary Containers proxy setting was not applied."
}
docker desktop start
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not start cleanly." }
docker desktop status
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not reach running state." }
$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -ne "same-as-host-proxy") {
    throw "Docker Desktop did not retain the temporary Same as host proxy setting."
}
```

Retry only the failed Docker operation. **Before the task ends, regardless of
success, failure, or cancellation, restore No proxy** with the following
cleanup. Do not send the final response while the temporary value or backup
still exists.

```powershell
$dockerSettingsDir = Join-Path $env:APPDATA "Docker"
$dockerSettingsPath = Join-Path $dockerSettingsDir "settings-store.json"
$dockerProxyBackupPath = Join-Path $dockerSettingsDir "settings-store.json.affogato-temporary-proxy.bak"

if (-not (Test-Path -LiteralPath $dockerProxyBackupPath -PathType Leaf)) {
    throw "The temporary proxy backup is missing; do not guess the original setting."
}

docker desktop stop
$dockerProcesses = @(
    Get-Process -Name "Docker Desktop", "com.docker.backend" -ErrorAction SilentlyContinue
)
if ($dockerProcesses.Count -ne 0) {
    throw "Docker Desktop is still running; do not restore its settings store yet."
}

$dockerProxyBackup = Get-Content -LiteralPath $dockerProxyBackupPath -Raw | ConvertFrom-Json
if ($dockerProxyBackup.ContainersProxyHTTPMode -ne "") {
    throw "The backup does not contain the expected original No proxy setting."
}
$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -notin @("", "same-as-host-proxy")) {
    throw "Refusing to overwrite a Containers proxy value changed by the user."
}
Copy-Item -LiteralPath $dockerProxyBackupPath -Destination $dockerSettingsPath -Force

$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -ne "") {
    throw "The original No proxy setting was not restored."
}
$dockerSettingsHash = (Get-FileHash -LiteralPath $dockerSettingsPath -Algorithm SHA256).Hash
$dockerProxyBackupHash = (Get-FileHash -LiteralPath $dockerProxyBackupPath -Algorithm SHA256).Hash
if ($dockerSettingsHash -ne $dockerProxyBackupHash) {
    throw "The restored settings do not exactly match the backup."
}

docker desktop start
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop did not start cleanly." }
docker desktop status
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop did not reach running state; keep the backup for recovery."
}
$dockerSettings = Get-Content -LiteralPath $dockerSettingsPath -Raw | ConvertFrom-Json
if ($dockerSettings.ContainersProxyHTTPMode -ne "") {
    throw "Docker Desktop did not retain the restored No proxy setting."
}

$resolvedDockerSettingsDir = (Resolve-Path -LiteralPath $dockerSettingsDir).Path
$resolvedDockerProxyBackupPath = (Resolve-Path -LiteralPath $dockerProxyBackupPath).Path
if (
    [IO.Path]::GetDirectoryName($resolvedDockerProxyBackupPath) -ne $resolvedDockerSettingsDir -or
    [IO.Path]::GetFileName($resolvedDockerProxyBackupPath) -ne "settings-store.json.affogato-temporary-proxy.bak"
) {
    throw "Refusing to remove an unexpected backup path."
}
Remove-Item -LiteralPath $resolvedDockerProxyBackupPath -Force
```

## Notes

- All repository text files must be UTF-8 without a BOM; run
  `python scripts/check_utf8.py` before committing.
- The local preflight writes its source SBOM under the ignored `.local-backups/`
  directory and must never replace release artifacts produced by GitHub Actions.
