"""从 sources.json 推导不可变的适配镜像标签，不下载、不构建、不推送。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_ADAPTER: Final = Path(__file__).resolve().parent
_COMMIT: Final = re.compile(r"[0-9a-f]{40}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_TAG: Final = re.compile(r"managed-[0-9a-f]{12}-[0-9a-f]{12}")
REPOSITORY: Final = "ghcr.io/vvv-345/cliproxyapi"
_FORBIDDEN_TAGS: Final = frozenset({"latest", "sha-bebf587f5a940af676f6e503065140d4991ae1f3"})


@dataclass(frozen=True, slots=True)
class AdapterPin:
    commit: str
    patch_sha256: str

    def __post_init__(self) -> None:
        if _COMMIT.fullmatch(self.commit) is None:
            raise ValueError("cliproxyapi commit must be 40 lowercase hex characters")
        if _SHA256.fullmatch(self.patch_sha256) is None:
            raise ValueError("patch sha256 must be 64 lowercase hex characters")

    @property
    def tag(self) -> str:
        tag: Final = f"managed-{self.commit[:12]}-{self.patch_sha256[:12]}"
        if tag in _FORBIDDEN_TAGS or _TAG.fullmatch(tag) is None:
            raise ValueError("refusing to publish a production or unscoped CLIProxyAPI tag")
        return tag

    @property
    def reference(self) -> str:
        return f"{REPOSITORY}:{self.tag}"


def load_pin(adapter: Path | None = None) -> AdapterPin:
    root: Final = _ADAPTER if adapter is None else adapter
    raw: Final = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("sources.json must be an object")
    cliproxy: Final = raw.get("cliproxyapi")
    patch: Final = raw.get("patch")
    if not isinstance(cliproxy, dict) or not isinstance(patch, dict):
        raise TypeError("sources.json is missing cliproxyapi or patch")
    commit: Final = cliproxy.get("commit")
    patch_file: Final = patch.get("file")
    patch_sha256: Final = patch.get("sha256")
    if not isinstance(commit, str) or not isinstance(patch_file, str) or not isinstance(patch_sha256, str):
        raise TypeError("sources.json pin fields must be strings")
    digest: Final = hashlib.sha256((root / patch_file).read_bytes()).hexdigest()
    if digest != patch_sha256:
        raise ValueError("补丁哈希与 sources.json 不一致")
    return AdapterPin(commit, patch_sha256)


def main() -> None:
    pin: Final = load_pin()
    sys.stdout.write(f"commit={pin.commit}\n")
    sys.stdout.write(f"tag={pin.tag}\n")
    sys.stdout.write(f"image={pin.reference}\n")


if __name__ == "__main__":
    main()
