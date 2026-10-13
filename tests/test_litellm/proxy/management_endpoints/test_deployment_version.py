"""执行发布版本脚本，验证固定版本系列与不可变部署标识。"""

import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "deploy" / "release_version.py"


@pytest.mark.parametrize(
    "version,revision,valid",
    [
        ("v1.1", "a" * 40, True),
        ("v1.1", "b" * 40, True),
        ("v1.1\nunsafe=value", "a" * 40, False),
        ("v1.1", "main", False),
    ],
)
def test_release_version_cli(tmp_path, version, revision, valid):
    version_file = tmp_path / "VERSION"
    version_file.write_text(version, encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), str(version_file), revision], capture_output=True, text=True)
    if valid:
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"deploy_tag={revision[:10]}",
            f"revision={revision}",
            "release_version=v1.1",
            f"versioned_tag=v1.1-{revision[:10]}",
        ]
    else:
        assert result.returncode != 0
        assert "deploy_tag=" not in result.stdout
