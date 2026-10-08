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
CVE_ID = re.compile(r"^CVE-\d{4}-\d+$")
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


def validate_grype_ignores(text: str, config: Path) -> list[str]:
    lines = text.splitlines()
    errors: list[str] = []
    active_lines = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if active_lines == ["ignore: []"]:
        return []
    try:
        ignore_line = lines.index("ignore:")
    except ValueError:
        return [f"{config}: missing ignore list"]

    starts = [
        index
        for index, line in enumerate(lines[ignore_line + 1 :], ignore_line + 1)
        if line.startswith("  - ")
    ]
    if not starts:
        return [f"{config}: ignore list must contain at least one scoped rule"]

    scopes: set[tuple[str, str, str, str, str, str]] = set()
    for rule_number, start in enumerate(starts, 1):
        end = starts[rule_number] if rule_number < len(starts) else len(lines)
        rule: dict[str, str] = {}
        package: dict[str, str] = {}
        in_package = False
        for line_number, line in enumerate(lines[start:end], start + 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if line_number == start + 1:
                match = re.fullmatch(r"  - vulnerability:\s*(\S+)", line)
                if match is None:
                    errors.append(f"{config}:{line_number}: ignore rule must start with a CVE")
                else:
                    rule["vulnerability"] = match.group(1)
                continue
            if line == "    package:":
                in_package = True
                continue
            target = package if line.startswith("      ") and in_package else rule
            indent = "      " if target is package else "    "
            match = re.fullmatch(rf"{indent}([a-z-]+):\s*(.+)", line)
            if match is None:
                errors.append(f"{config}:{line_number}: malformed or unscoped ignore field")
                continue
            key, value = match.groups()
            if key in target:
                errors.append(f"{config}:{line_number}: duplicate ignore field: {key}")
            target[key] = value

        vulnerability = rule.get("vulnerability", "")
        if not CVE_ID.fullmatch(vulnerability):
            errors.append(f"{config}: ignore rule {rule_number} must name one exact CVE")
        expected_rule_keys = {"vulnerability", "namespace", "match-type", "reason"}
        if set(rule) != expected_rule_keys:
            errors.append(
                f"{config}: ignore rule {rule_number} must contain exactly "
                "vulnerability, namespace, match-type, reason, and package"
            )
        if rule.get("namespace") != "nvd:cpe":
            errors.append(f"{config}: ignore rule {rule_number} must target only nvd:cpe")
        if rule.get("match-type") != "cpe-match":
            errors.append(f"{config}: ignore rule {rule_number} must target only cpe-match")
        if not rule.get("reason", "").strip():
            errors.append(f"{config}: ignore rule {rule_number} must explain its reachability")

        if set(package) != {"name", "version", "type"}:
            errors.append(
                f"{config}: ignore rule {rule_number} package must contain exact name, version, and type"
            )
        version = package.get("version", "")
        if not version or any(character in version for character in "*?<>|,"):
            errors.append(f"{config}: ignore rule {rule_number} package version must be exact")
        scope = (
            vulnerability,
            rule.get("namespace", ""),
            rule.get("match-type", ""),
            package.get("name", ""),
            version,
            package.get("type", ""),
        )
        if scope in scopes:
            errors.append(f"{config}: duplicate Grype ignore scope: {scope}")
        scopes.add(scope)
    return errors


def check_grype_ignores() -> list[str]:
    config = ROOT / ".grype.yaml"
    return validate_grype_ignores(config.read_text(encoding="utf-8"), config)


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
        'Invoke-NativeWithRetry "npm" @(',
        '"audit", "--audit-level=high",',
        '"--fetch-timeout=60000"',
        '"--fetch-retries=1"',
        '"--fetch-retry-mintimeout=5000"',
        '"--fetch-retry-maxtimeout=10000"',
        ") -Attempts 2",
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
        'reader_digest="$(docker buildx imagetools inspect "$candidate"',
        'immutable_candidate="${STAGING_IMAGE_NAME}@${reader_digest}"',
        'imagetools inspect --raw "$immutable_candidate"',
        'docker pull --platform linux/amd64 "$immutable_candidate"',
        'docker image rm "$immutable_candidate"',
        'docker pull --platform linux/arm64 "$immutable_candidate"',
        'vnd.docker.reference.type',
        'vnd.docker.reference.digest',
        'Refusing to overwrite existing immutable version tag',
        '"${STAGING_IMAGE_NAME}@${EXPECTED_DIGEST}"',
        'published_digest="$(docker buildx imagetools inspect',
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
    if "--metadata-file" in text:
        errors.append(
            f"{workflow}: imagetools create metadata files are not portable across buildx versions"
        )
    license_label = "org.opencontainers.image.licenses=MIT AND Apache-2.0"
    if text.count(license_label) < 2:
        errors.append(
            f"{workflow}: build and promotion metadata must preserve: {license_label}"
        )
    return errors


def main() -> int:
    errors = (
        check_actions()
        + check_base_images()
        + check_script_images()
        + check_release_compose()
        + check_python_build_system()
        + check_scanner_isolation()
        + check_grype_ignores()
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
