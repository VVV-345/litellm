"""本文件为根目录 pytest 注册独立号池测试包的导入路径。"""

import sys
from pathlib import Path
from typing import Final

_ACCOUNT_POOL_ROOT: Final = Path(__file__).resolve().parents[1]
if str(_ACCOUNT_POOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_ACCOUNT_POOL_ROOT))
