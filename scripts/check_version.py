from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_HEADING = re.compile(
    r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)


def validate_changelog(changelog: str, version: str, *, release: bool) -> None:
    headings = list(CHANGELOG_HEADING.finditer(changelog))
    assert any(match.group(1) == version for match in headings), (
        f"CHANGELOG.md has no ## [{version}] heading"
    )
    if not release:
        return

    unreleased = [match for match in headings if match.group(1) == "Unreleased"]
    assert len(unreleased) == 1, "CHANGELOG.md must contain exactly one ## [Unreleased] heading"
    unreleased_heading = unreleased[0]
    unreleased_index = headings.index(unreleased_heading)
    assert unreleased_index == 0, (
        "CHANGELOG.md must keep [Unreleased] before every versioned release"
    )
    assert sum(match.group(1) == version for match in headings) == 1, (
        f"CHANGELOG.md must contain exactly one ## [{version}] heading"
    )
    assert unreleased_index + 1 < len(headings), (
        "CHANGELOG.md must put the current release immediately after [Unreleased]"
    )
    current = headings[unreleased_index + 1]
    unreleased_body = changelog[unreleased_heading.end() : current.start()]
    assert not unreleased_body.strip(), (
        "CHANGELOG.md [Unreleased] must be empty for a formal release"
    )
    assert current.group(1) == version, (
        f"CHANGELOG.md must put ## [{version}] immediately after [Unreleased]"
    )
    release_date = current.group(2)
    assert release_date is not None, (
        f"CHANGELOG.md ## [{version}] must include a YYYY-MM-DD release date"
    )
    try:
        parsed_date = date.fromisoformat(release_date)
    except ValueError as error:
        raise AssertionError(
            f"CHANGELOG.md ## [{version}] has an invalid release date: {release_date}"
        ) from error
    assert parsed_date.isoformat() == release_date, (
        f"CHANGELOG.md ## [{version}] must use an exact YYYY-MM-DD release date"
    )


def validate_compose_release_version(compose: str, version: str) -> None:
    lines = compose.splitlines()
    section_indexes = [
        index
        for index, line in enumerate(lines)
        if line == "x-affogato-release:"
    ]
    assert len(section_indexes) == 1, (
        "compose.yaml must contain exactly one top-level x-affogato-release section"
    )
    section_index = section_indexes[0]
    section: list[str] = []
    for line in lines[section_index + 1 :]:
        if line and not line[0].isspace():
            break
        section.append(line)
    versions = []
    for line in section:
        match = re.fullmatch(
            r'\s+version:\s*(?P<quote>["\']?)(?P<value>[^"\'\s#]+)(?P=quote)\s*',
            line,
        )
        if match:
            versions.append(match.group("value"))
    assert versions == [version], (
        "compose.yaml x-affogato-release.version must exactly match VERSION: "
        f"expected {version!r}, found {versions!r}"
    )


def check_version(root: Path = ROOT, *, release: bool = False) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version

    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    assert package["version"] == version
    package_lock = json.loads(
        (root / "web/package-lock.json").read_text(encoding="utf-8")
    )
    assert package_lock["version"] == version
    assert package_lock["packages"][""]["version"] == version
    pyproject = (root / "backend/pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{version}"' in pyproject
    config = (root / "backend/app/config.py").read_text(encoding="utf-8")
    assert f'version: str = "{version}"' in config
    brand = (root / "web/src/brand.ts").read_text(encoding="utf-8")
    assert f'version: "{version}"' in brand
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    assert f"affogato-rss-reader:{version}" in compose
    validate_compose_release_version(compose, version)
    compose_dev = (root / "compose.dev.yaml").read_text(encoding="utf-8")
    assert f"VERSION: {version}" in compose_dev
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG VERSION={version}" in dockerfile
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"affogato-rss-reader-{version}.tar.gz" in readme
    backend_readme = (root / "backend/README.md").read_text(encoding="utf-8")
    assert f"v{version} distribution" in backend_readme
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    validate_changelog(changelog, version, release=release)

    migration_dir = root / "backend/alembic/versions"
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in migration_dir.glob("*.py"):
        content = path.read_text(encoding="utf-8")
        revision_match = re.search(r'^revision = "([^"]+)"$', content, re.MULTILINE)
        parent_match = re.search(r'^down_revision = "([^"]+)"$', content, re.MULTILINE)
        if revision_match:
            revisions.add(revision_match.group(1))
        if parent_match:
            parents.add(parent_match.group(1))
    heads = revisions - parents
    assert len(heads) == 1, heads
    migration_head = next(iter(heads))
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/container_smoke_test.py" in ci
    smoke = runpy.run_path(str(root / "scripts/container_smoke_test.py"))
    assert smoke["expected_revision"]() == migration_head
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release",
        action="store_true",
        help="require an empty Unreleased section followed by the dated current release",
    )
    args = parser.parse_args()
    print(check_version(release=args.release))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
