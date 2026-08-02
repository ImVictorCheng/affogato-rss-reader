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
$ReleaseImage = "ghcr.io/imvictorcheng/affogato-rss-reader:$Version"

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
        Invoke-Native "git" @("diff", "--check")
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
                Invoke-Native "docker" @(
                    "run", "--rm",
                    "--mount", $SourceMount,
                    "--mount", "type=volume,source=affogato-preflight-pip-cache,target=/root/.cache/pip",
                    "-w", "/workspace",
                    "python:$PythonVersion-slim",
                    "sh", "-c", $Script
                )
            }
        }
    }

    Invoke-Section "Web checks" {
        Push-Location (Join-Path $ProjectRoot "web")
        try {
            Invoke-Native "npm" @("ci")
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

    $Port = 18787
    $Amd64Image = $null
    foreach ($Platform in $Platforms) {
        if ($Platform -notmatch '^linux/(amd64|arm64)$') {
            throw "Unsupported preflight platform: $Platform"
        }
        $Architecture = $Matches[1]
        $Image = "affogato-rss-reader:preflight-$Architecture"
        if ($Architecture -eq "amd64") {
            $Amd64Image = $Image
        }
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
                Invoke-Native "docker" @(
                    "run", "--rm",
                    "-v", "/var/run/docker.sock:/var/run/docker.sock",
                    "--mount", $ConfigMount,
                    "anchore/grype:v0.116.1",
                    "docker:$Image",
                    "--fail-on", "high",
                    "--config", "/config/.grype.yaml"
                )
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

    if (-not $Amd64Image) {
        throw "The release bundle preflight requires linux/amd64 in -Platforms."
    }

    if (-not $OutputDirectory) {
        $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $OutputDirectory = Join-Path $ProjectRoot ".local-backups\release-preflight-$Version-$Stamp"
    }

    Invoke-Section "Release bundle" {
        Invoke-Native "docker" @("tag", $Amd64Image, $ReleaseImage)
        $Inspect = docker image inspect $ReleaseImage | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or -not $Inspect) {
            throw "Unable to inspect $ReleaseImage"
        }
        $RepositoryDigest = @($Inspect[0].RepoDigests | Where-Object { $_ -like "ghcr.io/imvictorcheng/affogato-rss-reader@*" })[0]
        if ($RepositoryDigest) {
            $ReaderDigest = $RepositoryDigest.Split("@", 2)[1]
        } else {
            $ReaderDigest = $Inspect[0].Id
        }
        if ($ReaderDigest -notmatch '^sha256:[0-9a-f]{64}$') {
            throw "The local release image has no usable sha256 digest."
        }
        Invoke-Native $Python @(
            "scripts/build_release_bundle.py",
            "--image-name", "ghcr.io/imvictorcheng/affogato-rss-reader",
            "--reader-digest", $ReaderDigest,
            "--output-dir", $OutputDirectory
        )

        Invoke-Native "docker" @(
            "run", "--rm",
            "--mount", "type=bind,source=$ProjectRoot,target=/src,readonly",
            "--mount", "type=bind,source=$OutputDirectory,target=/out",
            "anchore/syft:v1.39.0",
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
    }

    Write-Host "`nRelease preflight passed." -ForegroundColor Green
    Write-Host "Release bundle: $OutputDirectory"
} finally {
    Pop-Location
}
