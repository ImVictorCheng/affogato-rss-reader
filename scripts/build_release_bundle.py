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
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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
    docs = bundle / "docs"
    docs.mkdir()
    for name in (
        "BACKUP_AND_RESTORE.md",
        "ARCHITECTURE.md",
        "REVERSE_PROXY.md",
        "ROADMAP.md",
    ):
        shutil.copy2(ROOT / "docs" / name, docs / name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--reader-digest", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--skip-compose-validation", action="store_true")
    args = parser.parse_args()

    if not DIGEST_RE.fullmatch(args.reader_digest):
        raise RuntimeError("Reader digest must be an immutable sha256 digest")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._-]*/affogato-rss-reader", args.image_name):
        raise RuntimeError("Release image name is not an expected lowercase GHCR repository")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    bundle_name = f"affogato-rss-reader-{version}"
    output = args.output_dir.resolve()
    bundle = output / bundle_name
    archive = output / f"{bundle_name}.tar.gz"
    standalone_compose = output / f"affogato-rss-reader-compose-{version}.yaml"
    checksums = output / "SHA256SUMS"
    for path in (bundle, archive, standalone_compose, checksums):
        if path.exists():
            raise RuntimeError(f"Refusing to overwrite existing release output: {path}")

    output.mkdir(parents=True, exist_ok=True)
    bundle.mkdir()
    (bundle / "logs").mkdir()

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    compose = compose.replace("ghcr.io/OWNER/affogato-rss-reader", args.image_name)
    compose = compose.replace("READER_DIGEST", args.reader_digest)
    if "ghcr.io/OWNER/" in compose or "READER_DIGEST" in compose:
        raise RuntimeError("Release Compose still contains a publishing placeholder")
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

    checksum_text = (
        f"{sha256(archive)}  {archive.name}\n"
        f"{sha256(standalone_compose)}  {standalone_compose.name}\n"
    )
    checksums.write_text(checksum_text, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "archive": str(archive),
                "compose": str(standalone_compose),
                "checksums": str(checksums),
                "reader_digest": args.reader_digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
