"""验证 CPA 指纹对构建输入敏感，不依赖整站版本。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
from cpa_build_identity import build_key


def test_build_identity_is_stable_and_changes_for_base_or_architecture():
    builder, runtime = "golang@sha256:" + "a" * 64, "debian@sha256:" + "b" * 64
    original = build_key(builder, runtime, "amd64")
    assert original == build_key(builder, runtime, "amd64")
    assert original != build_key(builder, runtime, "arm64")
    assert original != build_key(builder, "debian@sha256:" + "c" * 64, "amd64")
    assert original != build_key("golang@sha256:" + "d" * 64, runtime, "amd64")


def test_build_identity_rejects_mutable_base():
    with pytest.raises(ValueError):
        build_key("golang:1.26-bookworm", "debian@sha256:" + "b" * 64, "amd64")


@pytest.mark.parametrize("filename", ["Dockerfile", "apply.py", "image_ref.py", "sources.json"])
def test_build_identity_changes_when_recipe_or_source_inputs_change(tmp_path, monkeypatch, filename):
    import shutil
    import cpa_build_identity
    import release_manifest
    adapter = tmp_path / "adapter"
    shutil.copytree(cpa_build_identity.ADAPTER, adapter)
    shutil.copy2(cpa_build_identity.ADAPTER.parent / "uv.lock", tmp_path / "uv.lock")
    monkeypatch.setattr(cpa_build_identity, "ADAPTER", adapter)
    monkeypatch.setattr(release_manifest, "ADAPTER", adapter)
    args = ("golang@sha256:" + "a" * 64, "debian@sha256:" + "b" * 64, "arm64")
    original = build_key(*args)
    path = adapter / filename
    path.write_bytes(path.read_bytes() + b"\n")
    assert build_key(*args) != original


def test_build_identity_cli_emits_only_selected_architectures():
    import json
    import subprocess
    result = subprocess.run([sys.executable, str(ROOT / "deploy/cpa_build_identity.py"),
        "--builder", "golang@sha256:" + "a" * 64, "--runtime", "debian@sha256:" + "b" * 64,
        "--architectures", "arm64"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert list(json.loads(result.stdout.removeprefix("build_keys="))) == ["arm64"]
