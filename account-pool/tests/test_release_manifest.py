"""发布清单必须有四个组件在所选架构上通过探测及固定补丁来源。"""

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


def _receipts(root: Path, architectures: tuple[str, ...] = ("arm64",)) -> None:
    for component in COMPONENTS:
        (root / f"{component}.json").write_text(json.dumps({
            "image": component, "digest": "sha256:" + "b" * 64,
            "platforms": [f"linux/{arch}" for arch in architectures],
        }), encoding="utf-8")
        for arch in architectures:
            (root / f"{component}-{arch}.json").write_text(json.dumps({
                "image": component, "arch": arch, "digest": "sha256:" + "c" * 64,
                "probe": "passed", "revision": REVISION,
            }), encoding="utf-8")


def _run(root: Path, architectures: str = "arm64") -> subprocess.CompletedProcess[str]:
    return subprocess.run([
        sys.executable, str(SCRIPT), "--revision", REVISION, "--version", "v1.9",
        "--architectures", architectures, "--digests", str(root), "--output", str(root / "release-manifest.json"),
    ], capture_output=True, text=True)


@pytest.mark.parametrize("architectures", [("arm64",), ("amd64",), ("amd64", "arm64")])
def test_release_manifest_records_all_components_and_adapter_provenance(
    tmp_path: Path, architectures: tuple[str, ...]
) -> None:
    _receipts(tmp_path, architectures)
    result: Final = _run(tmp_path, ",".join(architectures))
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
        assert image["platforms"] == [f"linux/{arch}" for arch in architectures]
        assert image["platform_digests"] == {f"linux/{arch}": "sha256:" + "c" * 64 for arch in architectures}
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


@pytest.mark.parametrize("component", COMPONENTS)
@pytest.mark.parametrize("broken", ["missing-receipt", "wrong-arch", "wrong-component", "extra-platform"])
def test_arm64_release_requires_a_verified_receipt_for_each_component(
    tmp_path: Path, component: str, broken: str
) -> None:
    _receipts(tmp_path)
    target: Final = tmp_path / f"{component}-arm64.json"
    if broken == "missing-receipt":
        target.unlink()
    elif broken == "extra-platform":
        manifest_path: Final = tmp_path / f"{component}.json"
        manifest: Final = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["platforms"] = ["linux/amd64", "linux/arm64"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        receipt: Final = json.loads(target.read_text(encoding="utf-8"))
        receipt["arch" if broken == "wrong-arch" else "image"] = "amd64" if broken == "wrong-arch" else "other"
        target.write_text(json.dumps(receipt), encoding="utf-8")
    result: Final = _run(tmp_path)
    assert result.returncode == 2
    assert "Release manifest rejected:" in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()


@pytest.mark.parametrize("architectures", ["", "arm64,arm64", "s390x", "amd64,", "arm64;echo bad"])
def test_release_manifest_rejects_invalid_architecture_selection(tmp_path: Path, architectures: str) -> None:
    _receipts(tmp_path)
    result: Final = _run(tmp_path, architectures)
    assert result.returncode == 2
    assert "architecture" in result.stderr.lower()
    assert not (tmp_path / "release-manifest.json").exists()


def test_dual_architecture_release_rejects_missing_amd64_probe(tmp_path: Path) -> None:
    _receipts(tmp_path, ("amd64", "arm64"))
    (tmp_path / "manager-amd64.json").unlink()
    result: Final = _run(tmp_path, "amd64,arm64")
    assert result.returncode == 2
    assert "manager-amd64.json" in result.stderr
    assert not (tmp_path / "release-manifest.json").exists()


@pytest.mark.parametrize(("selection", "expected"), [
    ("arm64", ["arm64"]), ("amd64", ["amd64"]),
    ("amd64,arm64", ["amd64", "arm64"]), ("arm64,amd64", ["amd64", "arm64"]),
])
def test_architecture_selector_emits_validated_matrix(selection: str, expected: list[str]) -> None:
    result: Final = subprocess.run(
        [sys.executable, str(ROOT / "deploy/release_platforms.py"), selection], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "architectures=" + json.dumps(expected, separators=(",", ":")) + "\n"


@pytest.mark.parametrize("selection", ["", "arm64,arm64", "s390x", "amd64,", "arm64;echo bad"])
def test_architecture_selector_rejects_invalid_input(selection: str) -> None:
    result: Final = subprocess.run(
        [sys.executable, str(ROOT / "deploy/release_platforms.py"), selection], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "architecture" in result.stderr.lower()
    assert not result.stdout
