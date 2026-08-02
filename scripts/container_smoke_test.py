#!/usr/bin/env python3
"""Run the release container smoke tests locally or in GitHub Actions."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(
    command: list[str],
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("+", subprocess.list2cmdline(command), flush=True)
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=capture,
    )


def docker(*arguments: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["docker", *arguments], capture=capture, check=check)


def expected_revision() -> str:
    versions = ROOT / "backend" / "alembic" / "versions"
    revisions = sorted(
        path.name.split("_", 1)[0]
        for path in versions.glob("[0-9][0-9][0-9][0-9]_*.py")
    )
    if not revisions:
        raise RuntimeError("No Alembic revisions were found")
    return revisions[-1]


def wait_for_json(url: str, attempts: int = 30) -> dict[str, object]:
    last_error: Exception | None = None
    # The smoke tests are loopback-only. Ignore any host proxy so requests
    # always reach the container directly; managed development environments
    # often inject a catch-all proxy that rejects loopback addresses.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(attempts):
        try:
            with opener.open(url, timeout=3) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def container_exists(name: str) -> bool:
    result = docker(
        "ps",
        "-a",
        "--filter",
        f"name=^/{name}$",
        "--format",
        "{{.Names}}",
        capture=True,
    )
    return bool(result.stdout.strip())


def database_state(container: str) -> dict[str, object]:
    code = """
import json, sqlite3
c = sqlite3.connect('/app/data/affogato-rss-reader.db')
print(json.dumps({
    'revision': c.execute('select version_num from alembic_version').fetchone()[0],
    'foreign_keys': c.execute('pragma foreign_key_check').fetchall(),
    'feeds': c.execute('select count(*) from feeds').fetchone()[0],
    'entries': c.execute('select count(*) from entries').fetchone()[0],
}))
""".strip()
    result = docker("exec", container, "python", "-c", code, capture=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def run_call_log_test(image: str) -> None:
    with tempfile.TemporaryDirectory(prefix="affogato-call-log-") as directory:
        mount = f"type=bind,source={Path(directory).resolve()},target=/app/logs"
        docker(
            "run",
            "--rm",
            "--user",
            "0:0",
            "--mount",
            mount,
            image,
            "sh",
            "-c",
            "chown 10001:10001 /app/logs && chmod 0755 /app/logs",
        )
        docker(
            "run",
            "--rm",
            "-e",
            "AFFOGATO_RSS_READER_CALL_LOG_FILE=/app/logs/llm-translation.jsonl",
            "--mount",
            mount,
            image,
            "python",
            "-c",
            (
                "from backend.app.call_logging import write_call_log; "
                'write_call_log(category="llm", operation="ci-smoke", '
                'status="success", duration_ms=0)'
            ),
        )
        log_path = Path(directory) / "llm-translation.jsonl"
        if not log_path.is_file() or log_path.stat().st_size == 0:
            raise RuntimeError("The Linux call-log bind mount smoke test produced no data")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--container", default="affogato-rss-reader-ci-local")
    parser.add_argument("--port", type=int, default=18787)
    args = parser.parse_args()

    if container_exists(args.container):
        raise RuntimeError(f"Refusing to replace existing container {args.container}")

    docker(
        "run",
        "--rm",
        args.image,
        "python",
        "-c",
        'import backend.app.update_runner; print("update helper import ok")',
    )

    started = False
    try:
        docker(
            "run",
            "-d",
            "--name",
            args.container,
            "-p",
            f"{args.port}:8787",
            "-e",
            "AFFOGATO_RSS_READER_AUTH_MODE=none",
            args.image,
        )
        started = True
        base_url = f"http://127.0.0.1:{args.port}/api/v1"
        health = wait_for_json(f"{base_url}/health")
        feeds = wait_for_json(f"{base_url}/feeds")
        state = database_state(args.container)

        if health.get("status") != "ok":
            raise RuntimeError(f"Unexpected health response: {health}")
        if len(feeds.get("items", [])) != 0:
            raise RuntimeError("The release image did not start with an empty feed library")
        if state["feeds"] != 0 or state["entries"] != 0:
            raise RuntimeError(f"The release database is not empty: {state}")
        if state["revision"] != expected_revision():
            raise RuntimeError(f"Unexpected Alembic revision: {state['revision']}")
        if state["foreign_keys"] != []:
            raise RuntimeError(f"Foreign-key violations found: {state['foreign_keys']}")

        # Alpine uses BusyBox grep; -q is portable while --quiet is not.
        docker(
            "exec",
            args.container,
            "grep",
            "-q",
            "MIT License",
            "/usr/share/licenses/affogato-rss-reader/LICENSE",
        )
        print(json.dumps({"health": health, "database": state}, sort_keys=True))
    finally:
        if started:
            logs = docker("logs", args.container, capture=True, check=False)
            if logs.stdout:
                print(logs.stdout, end="")
            if logs.stderr:
                print(logs.stderr, end="", file=sys.stderr)
            docker("rm", "-f", "-v", args.container, check=False)

    run_call_log_test(args.image)
    print("Container smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
