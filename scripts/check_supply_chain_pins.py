from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION_REF = re.compile(r"^[0-9a-f]{40}$")
ACTION_USE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$")
BASE_IMAGE = re.compile(r"^FROM\s+\S+@sha256:[0-9a-f]{64}(?:\s+AS\s+\S+)?$", re.IGNORECASE)
FROM_LINE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?$", re.IGNORECASE)
SYNTAX_IMAGE = re.compile(r"^# syntax=\S+@sha256:[0-9a-f]{64}$")


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


def main() -> int:
    errors = check_actions() + check_base_images()
    if errors:
        print("Supply-chain pin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("GitHub Actions and Docker base images are pinned to immutable identifiers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
