"""从成功发布标签计算产品版本，保留不可变提交身份，不修改远端状态。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final, Protocol, cast


class ReleaseArguments(Protocol):
    version_file: Path
    revision: str
    tags_file: Path | None
    bump: str


def version_parts(version: str) -> tuple[int, int, int]:
    parts: Final = tuple(int(part) for part in version.removeprefix("v").split("."))
    return parts[0], parts[1], parts[2] if len(parts) == 3 else 0


def main() -> int:
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("version_file", type=Path)
    parser.add_argument("revision")
    parser.add_argument("--tags-file", type=Path)
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    args: Final = cast(ReleaseArguments, parser.parse_args())  # cast-ok: argparse enforces the declared path types and choices.
    version: Final = args.version_file.read_text(encoding="utf-8").strip()
    revision: Final = args.revision
    if re.fullmatch(r"v[0-9]+\.[0-9]+(?:\.[0-9]+)?", version) is None or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        sys.stderr.write("Invalid release version or full commit\n")
        return 2
    tags: Final = args.tags_file.read_text(encoding="utf-8").splitlines() if args.tags_file else ()
    successful: Final = tuple(
        (match.group(1), match.group(2))
        for tag in tags
        if (match := re.fullmatch(r"(v[0-9]+\.[0-9]+(?:\.[0-9]+)?)-([0-9a-f]{10})", tag))
    )
    # 同一提交重跑沿用成功版本，失败构建没有成功标签，不消耗编号。
    existing: Final = max(
        (value for value, sha in successful if sha == revision[:10]), key=version_parts, default=None
    )
    major, minor, patch = max((version_parts(version), *(version_parts(value) for value, _ in successful)))
    # 显式提升 VERSION 系列时先发布该版本；成功后才从标签递增补丁号。
    requested_series: Final = bool(successful) and version_parts(version) > max(
        version_parts(value) for value, _ in successful
    )
    next_version: Final = existing or (
        version if args.bump == "patch" and requested_series else
        f"v{major + 1}.0" if args.bump == "major"
        else f"v{major}.{minor + 1}" if args.bump == "minor"
        else f"v{major}.{minor}.{patch + 1}"
    )
    sys.stdout.write(
        f"deploy_tag={revision[:10]}\nrevision={revision}\n"
        f"release_version={next_version}\nversioned_tag={next_version}-{revision[:10]}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
