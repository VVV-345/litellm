"""从成功构建的版本标签计算 cockpit 下一个发布版本，默认递增补丁位。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final, Protocol, cast

_VERSION: Final = re.compile(r"v([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")


class ReleaseArguments(Protocol):
    version_file: Path
    tags_file: Path
    head_tags_file: Path


def version_parts(version: str) -> tuple[int, int, int]:
    match: Final = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid release version: {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def rendered(parts: tuple[int, int, int]) -> str:
    major, minor, patch = parts
    return f"v{major}.{minor}.{patch}"


def parsed_versions(tags: tuple[str, ...]) -> tuple[tuple[tuple[int, int, int], str], ...]:
    return tuple((version_parts(tag), tag) for tag in tags if _VERSION.fullmatch(tag))


def next_version(version: str, tags: tuple[str, ...], head_tags: tuple[str, ...]) -> str:
    current: Final = version_parts(version)
    tagged_head: Final = parsed_versions(head_tags)
    if tagged_head:
        return max(tagged_head)[1]
    successful: Final = parsed_versions(tags)
    if not successful:
        return version
    latest: Final = max(parts for parts, _tag in successful)
    if current > latest:
        return version
    latest = max(current, latest)
    return rendered((latest[0], latest[1], latest[2] + 1))


def main() -> int:
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("version_file", type=Path)
    parser.add_argument("--tags-file", type=Path, required=True)
    parser.add_argument("--head-tags-file", type=Path, required=True)
    args: Final = cast(ReleaseArguments, parser.parse_args())
    version: Final = args.version_file.read_text(encoding="utf-8").strip()
    try:
        version_parts(version)
    except ValueError as error:
        sys.stderr.write(f"Invalid release version: {error}\n")
        return 2
    tags: Final = tuple(args.tags_file.read_text(encoding="utf-8").splitlines())
    head_tags: Final = tuple(args.head_tags_file.read_text(encoding="utf-8").splitlines())
    sys.stdout.write(f"release_version={next_version(version, tags, head_tags)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
