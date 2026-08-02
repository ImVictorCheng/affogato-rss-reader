from pathlib import Path
import json
import re
import runpy

root = Path(__file__).resolve().parents[1]
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
compose_dev = (root / "compose.dev.yaml").read_text(encoding="utf-8")
assert f"VERSION: {version}" in compose_dev
dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
assert f"ARG VERSION={version}" in dockerfile
readme = (root / "README.md").read_text(encoding="utf-8")
assert f"affogato-rss-reader-{version}.tar.gz" in readme
backend_readme = (root / "backend/README.md").read_text(encoding="utf-8")
assert f"v{version} distribution" in backend_readme
changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
assert f"## [{version}]" in changelog

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
print(version)
