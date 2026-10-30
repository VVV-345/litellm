"""校验发布架构选择，供构建矩阵和发布清单共用，不执行镜像构建。"""

from __future__ import annotations

import json
import sys
from typing import Final


def parse_architectures(value: str) -> tuple[str, ...]:
    architectures: Final = tuple(value.split(","))
    if (
        not set(architectures).issubset({"amd64", "arm64"})
        or len(architectures) != len(set(architectures))
    ):
        raise ValueError("architectures must be amd64, arm64, or amd64,arm64")
    return tuple(sorted(architectures))


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("expected one architecture selection")
        architectures: Final = parse_architectures(sys.argv[1])
    except ValueError as error:
        sys.stderr.write(f"Invalid architecture selection: {error}\n")
        return 2
    sys.stdout.write("architectures=" + json.dumps(architectures, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
