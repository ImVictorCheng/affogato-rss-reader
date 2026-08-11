from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.build_release_bundle import render_release_compose, write_checksums
from scripts.check_supply_chain_pins import (
    check_audit_gates,
    check_python_build_system,
    check_release_bundle_modes,
    check_release_compose,
    check_release_promotion,
    check_scanner_isolation,
    check_script_images,
)


ROOT = Path(__file__).resolve().parents[2]


class ReleaseSecurityTests(unittest.TestCase):
    def test_release_compose_uses_the_same_tagged_digest_for_every_service(self) -> None:
        digest = f"sha256:{'a' * 64}"
        repository = "ghcr.io/example/affogato-rss-reader"
        compose = render_release_compose(repository, digest)
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        expected = f"{repository}:{version}@{digest}"

        self.assertIn(f"x-reader-image: {expected}", compose)
        self.assertIn(f"reader-digest: {digest}", compose)
        self.assertEqual(
            len(
                re.findall(
                    rf"^\s+image:\s+{re.escape(expected)}\s*$",
                    compose,
                    re.MULTILINE,
                )
            ),
            3,
        )
        self.assertNotIn("&reader-image", compose)
        self.assertNotIn("*reader-image", compose)

    def test_release_checksums_include_source_sbom(self) -> None:
        artifacts = [
            Path("affogato-rss-reader.tar.gz"),
            Path("affogato-rss-reader-compose-v2.yaml"),
            Path("affogato-rss-reader-source.spdx.json"),
        ]
        checksums = Mock()
        with patch(
            "scripts.build_release_bundle.sha256",
            side_effect=["a" * 64, "b" * 64, "c" * 64],
        ):
            write_checksums(checksums, artifacts)
        checksums.write_text.assert_called_once_with(
            f"{'a' * 64}  affogato-rss-reader.tar.gz\n"
            f"{'b' * 64}  affogato-rss-reader-compose-v2.yaml\n"
            f"{'c' * 64}  affogato-rss-reader-source.spdx.json\n",
            encoding="utf-8",
            newline="\n",
        )

    def test_release_tool_images_and_compose_template_are_immutably_pinned(self) -> None:
        self.assertEqual(check_script_images(), [])
        self.assertEqual(check_release_compose(), [])
        self.assertEqual(check_python_build_system(), [])
        self.assertEqual(check_scanner_isolation(), [])
        self.assertEqual(check_audit_gates(), [])
        self.assertEqual(check_release_bundle_modes(), [])
        self.assertEqual(check_release_promotion(), [])

    def test_release_compose_rejects_non_digest_identifiers(self) -> None:
        with self.assertRaises(RuntimeError):
            render_release_compose(
                "ghcr.io/example/affogato-rss-reader",
                "sha256:local-docker-config-id",
            )


if __name__ == "__main__":
    unittest.main()
