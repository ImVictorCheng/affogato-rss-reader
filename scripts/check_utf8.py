from __future__ import annotations

import codecs
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "Dockerfile",
    "LICENSE",
    "VERSION",
}
SUSPICIOUS_MOJIBAKE = {
    "\ufffd": "Unicode replacement character",
    "\u951f\u65a4\u62f7": "common UTF-8/Chinese-codepage mojibake",
    "\u922b": "common UTF-8/Chinese-codepage mojibake",
    "\u923c": "common UTF-8/Chinese-codepage mojibake",
    "\u9286": "common UTF-8/Chinese-codepage mojibake",
    "\u8133": "common UTF-8/Chinese-codepage mojibake",
    "\u7ee0\u5d70\u5b05": "common UTF-8/Chinese-codepage mojibake",
    "\u93cd\u56e6\u8d1f": "common UTF-8/Chinese-codepage mojibake",
    'content: "\u8def"': "corrupted middle-dot CSS separator",
}


def repository_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    paths = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        path = ROOT / os.fsdecode(raw_path)
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES):
            paths.append(path)
    return paths


def main() -> int:
    problems: list[str] = []
    checked = 0
    for path in repository_files():
        relative = path.relative_to(ROOT)
        data = path.read_bytes()
        checked += 1
        if data.startswith(codecs.BOM_UTF8):
            problems.append(f"{relative}: UTF-8 BOM is not allowed; save as plain UTF-8")
            continue
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            problems.append(f"{relative}: invalid UTF-8 at byte {error.start}")
            continue
        for marker, description in SUSPICIOUS_MOJIBAKE.items():
            if marker in text:
                problems.append(f"{relative}: {description} ({marker!r})")

    if problems:
        print("UTF-8 validation failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"UTF-8 validation passed for {checked} text files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
