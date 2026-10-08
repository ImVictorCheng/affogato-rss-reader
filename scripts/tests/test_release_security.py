from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.build_release_bundle import render_release_compose, write_checksums
from scripts.check_version import validate_changelog, validate_compose_release_version
from scripts.check_supply_chain_pins import (
    check_audit_gates,
    check_grype_ignores,
    check_python_build_system,
    check_release_bundle_modes,
    check_release_compose,
    check_release_promotion,
    check_scanner_isolation,
    check_script_images,
    validate_grype_ignores,
)


ROOT = Path(__file__).resolve().parents[2]


class ReleaseSecurityTests(unittest.TestCase):
    def test_development_changelog_allows_unreleased_content(self) -> None:
        validate_changelog(
            "# Changelog\n\n## [Unreleased]\n\n- Work in progress\n\n"
            "## [1.2.3] - 2026-08-24\n",
            "1.2.3",
            release=False,
        )

    def test_formal_release_requires_empty_unreleased_and_next_dated_version(self) -> None:
        validate_changelog(
            "# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-08-24\n\n"
            "- Released work\n\n## [1.2.2] - 2026-08-01\n",
            "1.2.3",
            release=True,
        )
        invalid = (
            "# Changelog\n\n## [Unreleased]\n\n- Still unreleased\n\n"
            "## [1.2.3] - 2026-08-24\n"
        )
        with self.assertRaisesRegex(AssertionError, "must be empty"):
            validate_changelog(invalid, "1.2.3", release=True)

    def test_formal_release_rejects_wrong_order_or_invalid_date(self) -> None:
        unreleased_not_first = (
            "# Changelog\n\n## [1.2.2] - 2026-08-01\n\n## [Unreleased]\n\n"
            "## [1.2.3] - 2026-08-24\n"
        )
        with self.assertRaisesRegex(AssertionError, "before every versioned release"):
            validate_changelog(unreleased_not_first, "1.2.3", release=True)
        wrong_order = (
            "# Changelog\n\n## [Unreleased]\n\n## [1.2.2] - 2026-08-01\n\n"
            "## [1.2.3] - 2026-08-24\n"
        )
        with self.assertRaisesRegex(AssertionError, "immediately after"):
            validate_changelog(wrong_order, "1.2.3", release=True)
        invalid_date = "# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-02-30\n"
        with self.assertRaisesRegex(AssertionError, "invalid release date"):
            validate_changelog(invalid_date, "1.2.3", release=True)

    def test_compose_release_metadata_version_must_match_exactly(self) -> None:
        validate_compose_release_version(
            'name: reader\n\nx-affogato-release:\n  version: "1.2.3"\n'
            "  reader-digest: READER_DIGEST\n\nservices:\n  reader:\n",
            "1.2.3",
        )
        mismatched = (
            'name: reader\n\nx-affogato-release:\n  version: "1.2.2"\n'
            "  reader-digest: READER_DIGEST\n\nservices:\n  reader:\n"
        )
        with self.assertRaisesRegex(AssertionError, "must exactly match"):
            validate_compose_release_version(mismatched, "1.2.3")

    def test_formal_release_checks_require_release_metadata_and_annotated_tag(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        preflight = (ROOT / "scripts/release_preflight.ps1").read_text(encoding="utf-8")
        self.assertIn("python scripts/check_version.py --release", workflow)
        self.assertIn('tag_type="$(git cat-file -t "$GITHUB_REF")"', workflow)
        self.assertIn('[[ "$tag_type" != "tag" ]]', workflow)
        self.assertIn(
            'Invoke-Native $Python @("scripts/check_version.py", "--release")',
            preflight,
        )

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
        self.assertEqual(check_grype_ignores(), [])
        self.assertEqual(check_audit_gates(), [])
        self.assertEqual(check_release_bundle_modes(), [])
        self.assertEqual(check_release_promotion(), [])

    def test_grype_ignores_require_an_exact_reachable_scope(self) -> None:
        valid = (
            "ignore:\n"
            "  - vulnerability: CVE-2026-12345\n"
            "    namespace: nvd:cpe\n"
            "    match-type: cpe-match\n"
            "    reason: The vulnerable server API is not used.\n"
            "    package:\n"
            "      name: example\n"
            "      version: 1.2.3-r0\n"
            "      type: apk\n"
        )
        config = Path(".grype.yaml")
        self.assertEqual(validate_grype_ignores(valid, config), [])
        self.assertTrue(
            validate_grype_ignores(
                valid.replace("      version: 1.2.3-r0\n", ""),
                config,
            )
        )
        self.assertTrue(
            validate_grype_ignores(
                valid.replace("    namespace: nvd:cpe", "    namespace: alpine:distro"),
                config,
            )
        )

    def test_grype_allows_no_exceptions_without_accepting_extra_settings(self) -> None:
        config = Path(".grype.yaml")
        self.assertEqual(validate_grype_ignores("# No exceptions needed.\nignore: []\n", config), [])
        for invalid in ("ignore:\n", "ignore: []\nignore: []\n", "ignore: []\nfail-on-severity: negligible\n"):
            with self.subTest(config=invalid):
                self.assertTrue(validate_grype_ignores(invalid, config))

    def test_release_compose_rejects_non_digest_identifiers(self) -> None:
        with self.assertRaises(RuntimeError):
            render_release_compose(
                "ghcr.io/example/affogato-rss-reader",
                "sha256:local-docker-config-id",
            )


if __name__ == "__main__":
    unittest.main()
