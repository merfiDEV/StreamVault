"""Set StreamVault APP_VERSION before release builds."""

from __future__ import annotations

import re
import sys
from pathlib import Path


VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*"[0-9][^"]*"', re.MULTILINE)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/set_app_version.py <version>", file=sys.stderr)
        return 2

    version = sys.argv[1].strip().lstrip("v")
    if not VERSION_RE.fullmatch(version):
        print(f"Invalid version: {version}", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    version_file = root / "core" / "version.py"
    text = version_file.read_text(encoding="utf-8")
    updated, count = APP_VERSION_RE.subn(f'APP_VERSION = "{version}"', text, count=1)
    if count != 1:
        print("Could not find APP_VERSION assignment", file=sys.stderr)
        return 1

    version_file.write_text(updated, encoding="utf-8")
    print(f"APP_VERSION={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
