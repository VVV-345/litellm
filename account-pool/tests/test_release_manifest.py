"""发布清单必须有四个通过探测的双架构组件及固定补丁来源。"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[2]
SCRIPT: Final = ROOT / "deploy/release_manifest.py"
REVISION: Final = "a" * 40
COMPONENTS: Final = ("litellm", "manager", "browser-worker", "cliproxyapi")


def _receipts(root: Path) -> None:
    for component in COMPONENTS:
        (root / f"{component}.json").write_text(json.dumps({
            "image": component, "digest": "sha256:" + "b" * 64,
            "platforms": ["linux/amd64", "linux/arm64"],
        }), encoding="utf-8")
        for arch in ("amd64", "arm64"):
            (root / f"{component}-{arch}.json").write_text(json.dumps({
                "image": component, "arch": arch, "digest": "sha256:" + "c" * 64,
                "probe": "passed", "revision": REVISION,
            }), encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, str(SCRIPT), "--revision", REVISION, "--version", "v1.9",
        "--digests", str(root), "--output", str(root / "release-manifest.json"),
    ], capture_output=True, text=True)


def test_release_manifest_records_all_components_and_adapter_provenance(tmp_path: Path) -> None:
    _receipts(tmp_path)
    result: Final = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    manifest: Final = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == 2
    assert manifest["revision"] == REVISION
    assert manifest["versioned_tag"] == "v1.9-aaaaaaaaaa"
    assert set(manifest["images"]) == set(COMPONENTS)
    for component in COMPONENTS:
        image: Final = manifest["images"][component]
        assert image["image"] == f"ghcr.io/vvv-345/litellm-cliproxyapi:{component}-v1.9-aaaaaaaaaa"
        assert image["digest"] == "sha256:" + "b" * 64
        assert image["platforms"] == ["linux/amd64", "linux/arm64"]
        assert image["platform_digests"] == {"linux/amd64": "sha256:" + "c" * 64, "linux/arm64": "sha256:" + "c" * 64}
    assert manifest["images"]["litellm"]["source_revision"] == REVISION
    adapted: Final = manifest["images"]["cliproxyapi"]
    assert adapted["source_revision"] == "b3c954b63df09156ac2ac52ad038b3dc3a20748f"
    assert adapted["patch_sha256"] == "b1d310d5c106626ffe2177d7f2864fa540c08e1aebceeb6b049ef9e92c190e83"
    assert adapted["managed_identity_version"] == 1


@pytest.mark.parametrize("broken", ["missing-component", "missing-arch", "wrong-revision", "failed-probe", "wrong-platform", "bad-digest"])
def test_release_manifest_refuses_incomplete_or_unverified_images(tmp_path: Path, broken: str) -> None:
    _receipts(tmp_path)
    target: Final = tmp_path / "cliproxyapi-arm64.json"
    if broken == "missing-component":
        (tmp_path / "manager.json").unlink()
    elif broken == "missing-arch":
        target.unlink()
    elif broken in ("wrong-platform", "bad-digest"):
        manifest_path: Final = tmp_path / "manager.json"
        manifest: Final = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["platforms" if broken == "wrong-platform" else "digest"] = ["linux/amd64"] if broken == "wrong-platform" else "bad"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        receipt: Final = json.loads(target.read_text(encoding="utf-8"))
        receipt["revision" if broken == "wrong-revision" else "probe"] = "d" * 40 if broken == "wrong-revision" else "failed"
        target.write_text(json.dumps(receipt), encoding="utf-8")
    result: Final = _run(tmp_path)
    assert result.returncode == 2
    assert "Release manifest rejected:" in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()
