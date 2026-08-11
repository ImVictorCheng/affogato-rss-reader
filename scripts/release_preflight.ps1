[CmdletBinding()]
param(
    [string[]]$Platforms = @("linux/amd64", "linux/arm64"),
    [switch]$SkipPythonMatrix,
    [switch]$SkipVulnerabilityScan,
    [switch]$SkipE2E,
    [switch]$AllowDirty,
    [string]$OutputDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
$Revision = (git -C $ProjectRoot rev-parse HEAD).Trim()
$PythonImages = @{
    "3.12" = "python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36"
    "3.14" = "python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc"
}
$GrypeImage = "anchore/grype:v0.116.1@sha256:1e71065c0a4cff3e6bd3b8add525ffac4343eb4971694eb90a31cf6d4d3e85db"
$SyftImage = "anchore/syft:v1.39.0@sha256:6f13bb010923c33fb197047c8f88888e77071bd32596b3f605d62a133e493ce4"
$GrypeDbVolume = ""

if (Test-Path -LiteralPath (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} elseif (Test-Path -LiteralPath (Join-Path $ProjectRoot ".venv/bin/python")) {
    $Python = Join-Path $ProjectRoot ".venv/bin/python"
} else {
    $Python = "python"
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Write-Host "+ $File $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$File exited with code $LASTEXITCODE"
    }
}

function Invoke-NativeWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$Attempts = 3
    )
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            Invoke-Native $File $Arguments
            return
        } catch {
            if ($Attempt -eq $Attempts) { throw }
            $DelaySeconds = 5 * $Attempt
            Write-Warning "$File failed on attempt $Attempt/$Attempts; retrying in $DelaySeconds seconds."
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Invoke-Section {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Host "`n== $Name ==" -ForegroundColor Cyan
    & $Action
}

Push-Location $ProjectRoot
try {
    if (-not $AllowDirty -and (git status --porcelain)) {
        throw "The release preflight requires a clean worktree. Commit first or pass -AllowDirty while developing."
    }

    Invoke-Section "Static release checks" {
        Invoke-Native $Python @("scripts/check_version.py")
        Invoke-Native $Python @("scripts/check_utf8.py")
        Invoke-Native $Python @("scripts/check_supply_chain_pins.py")
        Invoke-Native $Python @("-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_*.py")
        Invoke-Native "git" @("diff", "--check")
    }

    Invoke-Section "Dependency and source security audit" {
        $SourceMount = "type=bind,source=$ProjectRoot,target=/workspace,readonly"
        $AuditScriptContent = @'
set -eu
python -m pip install pip-audit==2.10.1 bandit==1.9.4
python -m pip_audit --requirement /workspace/requirements.lock --progress-spinner off
python -m bandit -r /workspace/backend/app --severity-level high --confidence-level high
'@
        $AuditScript = Join-Path $env:TEMP ("affogato-security-audit-{0}.sh" -f (Get-Random))
        # PowerShell can preserve a CR before the final newline when the
        # here-string is materialized; strip all CR bytes so POSIX tools do
        # not receive values such as `high\r`.
        [IO.File]::WriteAllText(
            $AuditScript,
            $AuditScriptContent.Replace("`r", ""),
            [Text.Encoding]::ASCII
        )
        try {
            Invoke-Native "docker" @(
                "run", "--rm",
                "--mount", $SourceMount,
                "--mount", "type=volume,source=affogato-preflight-pip-cache,target=/root/.cache/pip",
                "--mount", "type=bind,source=$AuditScript,target=/tmp/security-audit.sh,readonly",
                "-w", "/workspace",
                $PythonImages["3.12"],
                "sh", "/tmp/security-audit.sh"
            )
        } finally {
            Remove-Item -LiteralPath $AuditScript -ErrorAction SilentlyContinue
        }
    }

    if (-not $SkipPythonMatrix) {
        Invoke-Section "Backend matrix (Python 3.12 and 3.14)" {
            $SourceMount = "type=bind,source=$ProjectRoot,target=/workspace,readonly"
            foreach ($PythonVersion in @("3.12", "3.14")) {
        $Script = @'
set -eu
mkdir -p /tmp/project
cp /workspace/README.md /workspace/LICENSE /tmp/project/
cp -R /workspace/backend /tmp/project/backend
python -m pip install -r /workspace/requirements-test.lock
python -m pip install --no-deps -e /tmp/project/backend
cd /tmp/project
pytest /workspace/backend/tests --cov=backend.app --cov-report=term-missing --basetemp=/tmp/pytest -p no:cacheprovider
'@
                # Windows PowerShell 5.1 can corrupt multi-line native arguments,
                # so deliver the matrix script to the container as a file instead.
                $MatrixScript = Join-Path $env:TEMP ("affogato-matrix-{0}.sh" -f (Get-Random))
                $Script.Replace("`r`n", "`n") | Set-Content -LiteralPath $MatrixScript -Encoding Ascii
                try {
                    Invoke-Native "docker" @(
                        "run", "--rm",
                        "--mount", $SourceMount,
                        "--mount", "type=volume,source=affogato-preflight-pip-cache,target=/root/.cache/pip",
                        "--mount", "type=bind,source=$MatrixScript,target=/tmp/matrix.sh,readonly",
                        "-w", "/workspace",
                        $PythonImages[$PythonVersion],
                        "sh", "/tmp/matrix.sh"
                    )
                } finally {
                    Remove-Item -LiteralPath $MatrixScript -ErrorAction SilentlyContinue
                }
            }
        }
    }

    Invoke-Section "Web checks" {
        Push-Location (Join-Path $ProjectRoot "web")
        try {
            Invoke-Native "npm" @("ci")
            Invoke-NativeWithRetry "npm" @(
                "audit", "--audit-level=high",
                "--fetch-timeout=60000",
                "--fetch-retries=1",
                "--fetch-retry-mintimeout=5000",
                "--fetch-retry-maxtimeout=10000"
            ) -Attempts 2
            Invoke-Native "npm" @("run", "check:ui")
            Invoke-Native "npm" @("run", "typecheck")
            Invoke-Native "npm" @("test")
            Invoke-Native "npm" @("run", "build")
            if (-not $SkipE2E) {
                Invoke-Native "npx" @("playwright", "install", "chromium")
                Invoke-Native "npm" @("run", "test:e2e")
            }
        } finally {
            Pop-Location
        }
    }

    if (-not $SkipVulnerabilityScan) {
        $GrypeDbVolume = "affogato-preflight-grype-db-{0}" -f (Get-Random)
        Invoke-Section "Refresh the pinned Grype database" {
            Invoke-Native "docker" @("volume", "create", $GrypeDbVolume)
            Invoke-NativeWithRetry "docker" @(
                "run", "--rm",
                "--read-only",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--mount", "type=volume,source=$GrypeDbVolume,target=/grype-db",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
                "--env", "GRYPE_DB_CACHE_DIR=/grype-db",
                $GrypeImage,
                "db", "update"
            )
        }
    }

    $Port = 18787
    foreach ($Platform in $Platforms) {
        if ($Platform -notmatch '^linux/(amd64|arm64)$') {
            throw "Unsupported preflight platform: $Platform"
        }
        $Architecture = $Matches[1]
        $Image = "affogato-rss-reader:preflight-$Architecture"
        Invoke-Section "Container $Platform" {
            Invoke-Native "docker" @(
                "buildx", "build",
                "--platform", $Platform,
                "--load",
                "--build-arg", "VERSION=$Version",
                "--build-arg", "VCS_REF=$Revision",
                "--build-arg", "SOURCE_URL=https://github.com/ImVictorCheng/affogato-rss-reader",
                "-t", $Image,
                "."
            )
            if (-not $SkipVulnerabilityScan) {
                $GrypeConfig = (Resolve-Path -LiteralPath (Join-Path $ProjectRoot ".grype.yaml")).Path
                $ConfigMount = "type=bind,source=$GrypeConfig,target=/config/.grype.yaml,readonly"
                $ScanArchive = Join-Path $env:TEMP ("affogato-grype-{0}-{1}.tar" -f $Architecture, (Get-Random))
                $ScanOutput = Join-Path $env:TEMP ("affogato-scan-{0}-{1}" -f $Architecture, (Get-Random))
                $ScanCheckScript = Join-Path $env:TEMP ("affogato-check-image-scan-{0}.py" -f (Get-Random))
                try {
                    New-Item -ItemType Directory -Path $ScanOutput -ErrorAction Stop | Out-Null
                    Invoke-Native "docker" @("image", "save", "--output", $ScanArchive, $Image)
                    $ArchiveMount = "type=bind,source=$ScanArchive,target=/scan/image.tar,readonly"
                    $OutputMount = "type=bind,source=$ScanOutput,target=/scan-output"
                    Invoke-Native "docker" @(
                        "run", "--rm",
                        "--network", "none",
                        "--read-only",
                        "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true",
                        "--mount", $ArchiveMount,
                        "--mount", $OutputMount,
                        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
                        "--env", "SYFT_CHECK_FOR_APP_UPDATE=false",
                        $SyftImage,
                        "scan", "docker-archive:/scan/image.tar",
                        "--output", "json=/scan-output/syft.json"
                    )
                    Invoke-Native "docker" @(
                        "run", "--rm",
                        "--network", "none",
                        "--read-only",
                        "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true",
                        "--mount", $ArchiveMount,
                        "--mount", $ConfigMount,
                        "--mount", $OutputMount,
                        "--mount", "type=volume,source=$GrypeDbVolume,target=/grype-db,readonly",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
                        "--env", "GRYPE_DB_CACHE_DIR=/grype-db",
                        "--env", "GRYPE_DB_AUTO_UPDATE=false",
                        "--env", "GRYPE_CHECK_FOR_APP_UPDATE=false",
                        $GrypeImage,
                        "docker-archive:/scan/image.tar",
                        "--fail-on", "high",
                        "--config", "/config/.grype.yaml",
                        "--output", "json",
                        "--file", "/scan-output/grype.json"
                    )
                    @'
import json
import sys
from pathlib import Path

syft = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
artifacts = syft.get("artifacts")
assert isinstance(artifacts, list) and artifacts, "Syft catalog contains no image packages"

grype = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert grype.get("descriptor", {}).get("name") == "grype", "Unexpected Grype result"
assert grype.get("source", {}).get("type") == "image", "Grype did not scan an image archive"
assert grype.get("source", {}).get("target"), "Grype result has no image target"
assert isinstance(grype.get("matches"), list), "Grype result has no matches collection"
print(f"image catalog ok: {len(artifacts)} packages, {len(grype['matches'])} vulnerability matches")
'@ | Set-Content -LiteralPath $ScanCheckScript -Encoding Ascii
                    Invoke-Native $Python @(
                        $ScanCheckScript,
                        (Join-Path $ScanOutput "syft.json"),
                        (Join-Path $ScanOutput "grype.json")
                    )
                } finally {
                    Remove-Item -LiteralPath $ScanArchive -Force -ErrorAction SilentlyContinue
                    Remove-Item -LiteralPath $ScanCheckScript -Force -ErrorAction SilentlyContinue
                    if ((Test-Path -LiteralPath $ScanOutput) -and
                        ([IO.Path]::GetFullPath($ScanOutput)).StartsWith(
                            [IO.Path]::GetFullPath($env:TEMP),
                            [StringComparison]::OrdinalIgnoreCase
                        )) {
                        Remove-Item -LiteralPath $ScanOutput -Recurse -Force -ErrorAction SilentlyContinue
                    }
                }
            }
            Invoke-Native $Python @(
                "scripts/container_smoke_test.py",
                "--image", $Image,
                "--container", "affogato-preflight-$Architecture",
                "--port", "$Port"
            )
        }
        $Port += 1
    }

    if (-not $OutputDirectory) {
        $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $OutputDirectory = Join-Path $ProjectRoot ".local-backups\release-preflight-$Version-$Stamp"
    }
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null
    }

    Invoke-Section "Release structure and source SBOM" {
        Invoke-Native "docker" @(
            "run", "--rm",
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--mount", "type=bind,source=$ProjectRoot,target=/src,readonly",
            "--mount", "type=bind,source=$OutputDirectory,target=/out",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
            "--env", "SYFT_CHECK_FOR_APP_UPDATE=false",
            $SyftImage,
            "scan", "dir:/src",
            "-o", "spdx-json=/out/affogato-rss-reader-source.spdx.json",
            "--exclude", "./.git/**",
            "--exclude", "./node_modules/**",
            "--exclude", "./.venv/**",
            "--exclude", "./.local-backups/**",
            "--exclude", "./logs/**",
            "--exclude", "./web/dist/**",
            "--exclude", "./web/test-results/**",
            "--exclude", "./**/__pycache__/**",
            "--exclude", "./backend/static/**",
            "--exclude", "./backend/data/**",
            "--exclude", "./data/**",
            "--exclude", "./.pytest_cache/**",
            "--exclude", "./.coverage"
        )
        $SourceSbom = Join-Path $OutputDirectory "affogato-rss-reader-source.spdx.json"
        # Windows PowerShell 5.1 corrupts multi-line -c scripts containing
        # quotes, so validate the SBOM from a temporary script file instead.
        $SbomCheckScript = Join-Path $env:TEMP ("affogato-check-sbom-{0}.py" -f (Get-Random))
        @'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
assert str(data.get("spdxVersion", "")).startswith("SPDX-"), data.get("spdxVersion")
assert data.get("packages"), "The source SBOM contains no packages"
print(f"source SBOM ok: {data['spdxVersion']}, {len(data['packages'])} packages")
'@ | Set-Content -LiteralPath $SbomCheckScript -Encoding Ascii
        try {
            Invoke-Native $Python @($SbomCheckScript, $SourceSbom)
        } finally {
            Remove-Item -LiteralPath $SbomCheckScript -ErrorAction SilentlyContinue
        }

        Invoke-Native $Python @(
            "scripts/build_release_bundle.py",
            "--image-name", "ghcr.io/imvictorcheng/affogato-rss-reader",
            "--validate-compose-template-only"
        )
    }

    Write-Host "`nRelease preflight passed." -ForegroundColor Green
    Write-Host "Preflight source SBOM: $OutputDirectory"
} finally {
    if ($GrypeDbVolume) {
        & docker volume rm -f $GrypeDbVolume 2>$null | Out-Null
    }
    Pop-Location
}
