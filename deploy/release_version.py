"""解析固定产品版本与不可变提交标签，不修改 Git 标签或远端状态。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Final


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: release_version.py VERSION_FILE FULL_COMMIT\n")
        return 2
    version: Final = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    revision: Final = sys.argv[2]
    if re.fullmatch(r"v[0-9]+\.[0-9]+", version) is None or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        sys.stderr.write("Invalid release version or full commit\n")
        return 2
    sys.stdout.write(
        f"deploy_tag={revision[:10]}\nrevision={revision}\nrelease_version={version}\nversioned_tag={version}-{revision[:10]}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
