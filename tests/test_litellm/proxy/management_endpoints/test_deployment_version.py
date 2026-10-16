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
            "release_version=v1.1.1",
            f"versioned_tag=v1.1.1-{revision[:10]}",
        ]
    else:
        assert result.returncode != 0
        assert "deploy_tag=" not in result.stdout


@pytest.mark.parametrize("tags,bump,want", [
    ("", "patch", "v1.2.1"),
    ("v1.2.1-1111111111\nv1.2.2-2222222222", "patch", "v1.2.3"),
    ("v1.2.9-1111111111\nv1.2.10-2222222222", "patch", "v1.2.11"),
    ("v1.2.2-1111111111", "minor", "v1.3"),
    ("v1.2.2-1111111111", "major", "v2.0"),
    ("v2.0-1111111111", "patch", "v2.0.1"),
    ("v1.2.2-aaaaaaaaaa", "patch", "v1.2.2"),
    ("v1.2.1-1111111111\ncandidate-v9.9.9", "patch", "v1.2.2"),
])
def test_successful_release_sequence(tmp_path, tags, bump, want):
    version = tmp_path / "VERSION"
    version.write_text("v1.2", encoding="utf-8")
    successful = tmp_path / "tags.txt"
    successful.write_text(tags, encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(version), "a" * 40,
               "--tags-file", str(successful), "--bump", bump]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert f"release_version={want}\n" in first.stdout
    # 失败的构建没有成功标签，重新计算不得消耗版本号。
    assert second.stdout == first.stdout
    assert successful.read_text(encoding="utf-8") == tags


@pytest.mark.parametrize("tags,want", [
    ("v1.2-1111111111", "v1.3"),
    ("v1.2.2-1111111111", "v1.3"),
    ("v1.3-1111111111", "v1.3.1"),
    ("v1.3.1-1111111111", "v1.3.2"),
])
def test_explicit_release_series_starts_at_requested_version(tmp_path, tags, want):
    version = tmp_path / "VERSION"
    version.write_text("v1.3", encoding="utf-8")
    successful = tmp_path / "tags.txt"
    successful.write_text(tags, encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(version), "a" * 40, "--tags-file", str(successful)]
    first = subprocess.run(command, capture_output=True, text=True)
    retry = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert f"release_version={want}\n" in first.stdout
    assert retry.stdout == first.stdout
    assert successful.read_text(encoding="utf-8") == tags


def test_plan_a_product_release_starts_at_v16(tmp_path):
    version = SCRIPT.parent / "VERSION"
    assert version.read_text(encoding="utf-8").strip() == "v1.6"
    successful = tmp_path / "tags.txt"
    successful.write_text("v1.3.9-1111111111\nv1.4.2-2222222222\n", encoding="utf-8")
    command = [sys.executable, str(SCRIPT), str(version), "a" * 40, "--tags-file", str(successful)]
    first = subprocess.run(command, capture_output=True, text=True)
    retry = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert "release_version=v1.6\n" in first.stdout
    assert "versioned_tag=v1.6-aaaaaaaaaa\n" in first.stdout
    assert retry.stdout == first.stdout
    successful.write_text("v1.6-1111111111\n", encoding="utf-8")
    following = subprocess.run(command, capture_output=True, text=True)
    assert following.returncode == 0, following.stderr
    assert "release_version=v1.6.1\n" in following.stdout
