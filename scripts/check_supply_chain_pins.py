from __future__ import annotations

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION_REF = re.compile(r"^[0-9a-f]{40}$")
ACTION_USE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$")
BASE_IMAGE = re.compile(r"^FROM\s+\S+@sha256:[0-9a-f]{64}(?:\s+AS\s+\S+)?$", re.IGNORECASE)
FROM_LINE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?$", re.IGNORECASE)
SYNTAX_IMAGE = re.compile(r"^# syntax=\S+@sha256:[0-9a-f]{64}$")
TOOL_IMAGE = re.compile(r"(?P<image>(?:anchore/(?:grype|syft)|python):[^\s\"']+)")
PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
RELEASE_IMAGE_TEMPLATE = re.compile(
    r"^x-reader-image:\s+"
    r"ghcr\.io/OWNER/affogato-rss-reader:\d+\.\d+\.\d+@READER_DIGEST$",
    re.MULTILINE,
)


def check_actions() -> list[str]:
    errors: list[str] = []
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    for workflow in workflows:
        for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = ACTION_USE.match(line)
            if not match:
                continue
            target, version_comment = match.groups()
            if target.startswith("./"):
                continue
            if "@" not in target:
                errors.append(f"{workflow}:{line_number}: action has no ref: {target}")
                continue
            action, ref = target.rsplit("@", 1)
            if not ACTION_REF.fullmatch(ref):
                errors.append(f"{workflow}:{line_number}: {action} is not pinned to a full commit SHA")
            if not version_comment:
                errors.append(f"{workflow}:{line_number}: pinned action is missing a version comment")
    return errors


def check_base_images() -> list[str]:
    errors: list[str] = []
    dockerfile = ROOT / "Dockerfile"
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    if not lines or not SYNTAX_IMAGE.fullmatch(lines[0]):
        errors.append(f"{dockerfile}:1: Dockerfile frontend is not pinned to a sha256 digest")
    stages: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.startswith("FROM "):
            continue
        parsed = FROM_LINE.fullmatch(line)
        if not parsed:
            errors.append(f"{dockerfile}:{line_number}: malformed FROM instruction")
            continue
        source, stage = parsed.groups()
        if source.lower() not in stages and not BASE_IMAGE.fullmatch(line):
            errors.append(f"{dockerfile}:{line_number}: base image is not pinned to a sha256 digest")
        if stage:
            stages.add(stage.lower())
    return errors


def check_script_images() -> list[str]:
    errors: list[str] = []
    scripts = sorted((ROOT / "scripts").rglob("*"))
    for script in scripts:
        if script.suffix.lower() not in {".ps1", ".py", ".sh"}:
            continue
        for line_number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
            for match in TOOL_IMAGE.finditer(line):
                image = match.group("image").rstrip(",)")
                if not PINNED_IMAGE.fullmatch(image):
                    errors.append(
                        f"{script}:{line_number}: tool image is not pinned to a sha256 digest: {image}"
                    )
    return errors


def check_release_compose() -> list[str]:
    compose = ROOT / "compose.yaml"
    text = compose.read_text(encoding="utf-8")
    errors: list[str] = []
    if not RELEASE_IMAGE_TEMPLATE.search(text):
        errors.append(
            f"{compose}: release image template must combine the version tag with @READER_DIGEST"
        )
    image_reference = re.compile(
        r"^\s+image:\s+ghcr\.io/OWNER/affogato-rss-reader:"
        r"\d+\.\d+\.\d+@READER_DIGEST\s*$",
        re.MULTILINE,
    )
    if len(image_reference.findall(text)) != 3:
        errors.append(f"{compose}: every release service must use the immutable image")
    if "&reader-image" in text or "*reader-image" in text:
        errors.append(f"{compose}: release service images must not rely on YAML aliases")
    if not re.search(r"^\s+reader-digest:\s+READER_DIGEST\s*$", text, re.MULTILINE):
        errors.append(f"{compose}: release metadata must expose the same READER_DIGEST")
    if 'profiles: ["release-updates"]' not in text:
        errors.append(f"{compose}: the Docker-socket updater must be behind an opt-in profile")
    return errors


def check_python_build_system() -> list[str]:
    pyproject = ROOT / "backend" / "pyproject.toml"
    document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    build_system = document.get("build-system")
    if not isinstance(build_system, dict):
        return [f"{pyproject}: missing build-system requirements"]
    requirements = build_system.get("requires")
    if not isinstance(requirements, list) or not all(
        isinstance(requirement, str) for requirement in requirements
    ):
        return [f"{pyproject}: invalid build-system requirements"]
    expected = {"setuptools", "wheel"}
    names: set[str] = set()
    errors: list[str] = []
    for requirement in requirements:
        pinned = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", requirement)
        if pinned is None:
            errors.append(f"{pyproject}: build requirement is not exactly pinned: {requirement}")
            continue
        names.add(pinned.group(1).lower())
    if names != expected:
        errors.append(f"{pyproject}: build-system must pin exactly setuptools and wheel")
    return errors


def check_scanner_isolation() -> list[str]:
    preflight = ROOT / "scripts" / "release_preflight.ps1"
    text = preflight.read_text(encoding="utf-8")
    errors: list[str] = []
    if "/var/run/docker.sock" in text:
        errors.append(f"{preflight}: vulnerability scanner must not mount the Docker socket")
    if '"file:/scan/image.tar"' in text:
        errors.append(f"{preflight}: an image archive must not be scanned as a plain file")
    if (
        text.count('"docker-archive:/scan/image.tar"') < 2
        or 'target=/scan/image.tar,readonly' not in text
    ):
        errors.append(f"{preflight}: vulnerability scanner must use a read-only image archive")
    for requirement in (
        '"--network", "none"',
        '"--read-only"',
        '"--cap-drop", "ALL"',
        '"--security-opt", "no-new-privileges:true"',
        '"GRYPE_DB_AUTO_UPDATE=false"',
        'artifacts, list) and artifacts',
        'get("source", {}).get("type") == "image"',
    ):
        if requirement not in text:
            errors.append(f"{preflight}: isolated archive scan is missing: {requirement}")
    return errors


def check_audit_gates() -> list[str]:
    ci = ROOT / ".github" / "workflows" / "ci.yml"
    preflight = ROOT / "scripts" / "release_preflight.ps1"
    ci_text = ci.read_text(encoding="utf-8")
    preflight_text = preflight.read_text(encoding="utf-8")
    errors: list[str] = []
    requirements = ("pip-audit==2.10.1", "bandit==1.9.4")
    commands = (
        "python -m pip_audit --requirement requirements.lock --progress-spinner off",
        "python -m bandit -r backend/app --severity-level high --confidence-level high",
        "npm audit --audit-level=high",
    )
    for requirement in requirements:
        if requirement not in ci_text:
            errors.append(f"{ci}: missing pinned audit tool {requirement}")
        if requirement not in preflight_text:
            errors.append(f"{preflight}: missing pinned audit tool {requirement}")
    for command in commands:
        if command not in ci_text:
            errors.append(f"{ci}: missing audit gate command: {command}")
    preflight_commands = (
        "python -m pip_audit --requirement /workspace/requirements.lock --progress-spinner off",
        "python -m bandit -r /workspace/backend/app --severity-level high --confidence-level high",
        'Invoke-Native "npm" @("audit", "--audit-level=high")',
    )
    for command in preflight_commands:
        if command not in preflight_text:
            errors.append(f"{preflight}: missing audit gate command: {command}")
    return errors


def check_release_bundle_modes() -> list[str]:
    preflight = ROOT / "scripts" / "release_preflight.ps1"
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    preflight_text = preflight.read_text(encoding="utf-8")
    workflow_text = workflow.read_text(encoding="utf-8")
    errors: list[str] = []
    if '"--validate-compose-template-only"' not in preflight_text:
        errors.append(f"{preflight}: local validation must use the non-artifact template mode")
    if '"--reader-digest"' in preflight_text or "$Inspect[0].Id" in preflight_text:
        errors.append(f"{preflight}: local image IDs must never stand in for registry digests")
    for argument in ("--reader-digest", "--source-sbom"):
        if argument not in workflow_text:
            errors.append(f"{workflow}: formal release bundle is missing {argument}")
    return errors


def check_release_promotion() -> list[str]:
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    text = workflow.read_text(encoding="utf-8")
    errors: list[str] = []
    requirements = (
        "staging-image-name",
        "affogato-rss-reader-staging",
        'outputs: type=image,name=${{ needs.validate.outputs.staging-image-name }}',
        '--metadata-file "$metadata_file"',
        'imagetools inspect --raw "$candidate"',
        'docker pull --platform linux/amd64 "$candidate"',
        'docker pull --platform linux/arm64 "$candidate"',
        'vnd.docker.reference.type',
        'vnd.docker.reference.digest',
        'Refusing to overwrite existing immutable version tag',
        '"${STAGING_IMAGE_NAME}@${EXPECTED_DIGEST}"',
        'copied_digest="$(jq -er',
    )
    for requirement in requirements:
        if requirement not in text:
            errors.append(f"{workflow}: release promotion is missing: {requirement}")
    unsafe_formal_build = (
        "outputs: type=image,name=${{ needs.validate.outputs.image-name }},"
        "push-by-digest=true"
    )
    if unsafe_formal_build in text:
        errors.append(f"{workflow}: unverified platform images must be pushed only to staging")
    if text.count("--metadata-file") < 2:
        errors.append(f"{workflow}: candidate creation and final promotion must record metadata")
    return errors


def main() -> int:
    errors = (
        check_actions()
        + check_base_images()
        + check_script_images()
        + check_release_compose()
        + check_python_build_system()
        + check_scanner_isolation()
        + check_audit_gates()
        + check_release_bundle_modes()
        + check_release_promotion()
    )
    if errors:
        print("Supply-chain pin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Actions, Docker bases, build backends, release Compose, and isolated script tools "
        "are immutably pinned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
