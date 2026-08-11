#!/usr/bin/env python3
"""Build the Compose release bundle used locally and by GitHub Actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCE_SBOM_NAME = "affogato-rss-reader-source.spdx.json"
STRUCTURE_ONLY_DIGEST = f"sha256:{'0' * 64}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_release_files(bundle: Path) -> None:
    for name in (
        ".env.example",
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        shutil.copy2(ROOT / name, bundle / name)
    licenses = bundle / "licenses"
    licenses.mkdir()
    mathjax_license = ROOT / "licenses" / "MathJax-APACHE-2.0.txt"
    license_text = mathjax_license.read_text(encoding="utf-8")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        raise RuntimeError("The bundled MathJax Apache-2.0 license is invalid")
    shutil.copy2(mathjax_license, licenses / mathjax_license.name)
    docs = bundle / "docs"
    docs.mkdir()
    for name in (
        "BACKUP_AND_RESTORE.md",
        "ARCHITECTURE.md",
        "REVERSE_PROXY.md",
        "ROADMAP.md",
    ):
        shutil.copy2(ROOT / "docs" / name, docs / name)


def render_release_compose(image_name: str, reader_digest: str) -> str:
    if not DIGEST_RE.fullmatch(reader_digest):
        raise RuntimeError("Reader digest must be an immutable sha256 digest")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._-]*/affogato-rss-reader", image_name):
        raise RuntimeError("Release image name is not an expected lowercase GHCR repository")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    compose = compose.replace("ghcr.io/OWNER/affogato-rss-reader", image_name)
    compose = compose.replace("READER_DIGEST", reader_digest)
    expected_image = f"{image_name}:{version}@{reader_digest}"
    expected_declaration = f"x-reader-image: {expected_image}"
    if "ghcr.io/OWNER/" in compose or "READER_DIGEST" in compose:
        raise RuntimeError("Release Compose still contains a publishing placeholder")
    if expected_declaration not in compose:
        raise RuntimeError("Release Compose does not use the immutable release image")
    if len(
        re.findall(
            rf"^\s+image:\s+{re.escape(expected_image)}\s*$",
            compose,
            re.MULTILINE,
        )
    ) != 3:
        raise RuntimeError("Every release service must use the immutable image reference")
    return compose


def write_checksums(checksums: Path, artifacts: list[Path]) -> None:
    checksums.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in artifacts),
        encoding="utf-8",
        newline="\n",
    )


def validate_compose_structure(image_name: str) -> None:
    compose = render_release_compose(image_name, STRUCTURE_ONLY_DIGEST)
    with tempfile.TemporaryDirectory(prefix="affogato-compose-structure-") as directory:
        compose_path = Path(directory) / "compose.yaml"
        compose_path.write_text(compose, encoding="utf-8", newline="\n")
        subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "config", "--quiet"],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--reader-digest")
    parser.add_argument("--source-sbom", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--skip-compose-validation", action="store_true")
    parser.add_argument("--validate-compose-template-only", action="store_true")
    args = parser.parse_args()

    if args.validate_compose_template_only:
        if args.reader_digest or args.source_sbom or args.skip_compose_validation:
            parser.error(
                "template-only validation cannot accept release digests, SBOMs, or skip validation"
            )
        validate_compose_structure(args.image_name)
        print(json.dumps({"compose_template_valid": True, "release_artifacts_created": False}))
        return 0
    if args.reader_digest is None or args.source_sbom is None:
        parser.error("formal release bundles require --reader-digest and --source-sbom")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    bundle_name = f"affogato-rss-reader-{version}"
    output = args.output_dir.resolve()
    bundle = output / bundle_name
    archive = output / f"{bundle_name}.tar.gz"
    standalone_compose = output / f"affogato-rss-reader-compose-v2-{version}.yaml"
    checksums = output / "SHA256SUMS"
    source_sbom_input = args.source_sbom.resolve()
    source_sbom = output / SOURCE_SBOM_NAME
    if source_sbom_input.name != SOURCE_SBOM_NAME or not source_sbom_input.is_file():
        raise RuntimeError(f"Source SBOM must be an existing {SOURCE_SBOM_NAME} file")
    for path in (bundle, archive, standalone_compose, checksums):
        if path.exists():
            raise RuntimeError(f"Refusing to overwrite existing release output: {path}")

    output.mkdir(parents=True, exist_ok=True)
    bundle.mkdir()
    (bundle / "logs").mkdir()

    if source_sbom_input != source_sbom.resolve():
        if source_sbom.exists():
            raise RuntimeError(f"Refusing to overwrite existing release output: {source_sbom}")
        shutil.copy2(source_sbom_input, source_sbom)

    compose = render_release_compose(args.image_name, args.reader_digest)
    compose_path = bundle / "compose.yaml"
    compose_path.write_text(compose, encoding="utf-8", newline="\n")
    shutil.copy2(compose_path, standalone_compose)
    copy_release_files(bundle)

    if not args.skip_compose_validation:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "config", "--quiet"],
            check=True,
        )

    with tarfile.open(archive, "w:gz") as target:
        target.add(bundle, arcname=bundle_name)

    write_checksums(checksums, [archive, standalone_compose, source_sbom])
    print(
        json.dumps(
            {
                "archive": str(archive),
                "compose": str(standalone_compose),
                "checksums": str(checksums),
                "source_sbom": str(source_sbom),
                "reader_digest": args.reader_digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
