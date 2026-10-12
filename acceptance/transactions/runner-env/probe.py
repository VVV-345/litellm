"""对指定验收执行器运行同一子进程输入，检查数据库测试是否保持显式选择。"""

import importlib.util
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("acceptance_probe_runner", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    result = module.execute(
        "database-opt-in",
        [sys.executable, "-c", "import os; print('absent' if 'ACCOUNT_POOL_TEST_DATABASE_URL' not in os.environ else 'present')"],
        root,
        root,
    )
    value = (root / "database-opt-in.stdout.txt").read_text().strip()
    print(f"ACCOUNT_POOL_TEST_DATABASE_URL={value}")
    print(f"CHILD_EXIT={result['exit_code']}")
    raise SystemExit(result["exit_code"] or 0)
