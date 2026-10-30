"""将固定版本适配补丁应用到干净的 CLIProxyAPI 源码，不下载、不构建、不部署。"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict


class SourcePin(BaseModel):
    model_config = ConfigDict(frozen=True)
    commit: str


class PatchPin(BaseModel):
    model_config = ConfigDict(frozen=True)
    file: str
    sha256: str


class Sources(BaseModel):
    model_config = ConfigDict(frozen=True)
    cliproxyapi: SourcePin
    patch: PatchPin


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python apply.py SOURCE_DIR")
    source: Final = Path(sys.argv[1]).resolve()
    adapter: Final = Path(__file__).resolve().parent
    manifest: Final = Sources.model_validate_json((adapter / "sources.json").read_bytes())
    patch: Final = adapter / manifest.patch.file
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest.patch.sha256:
        raise SystemExit("补丁哈希与 sources.json 不一致")
    head: Final = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if head != manifest.cliproxyapi.commit:
        raise SystemExit("源码提交与 sources.json 不一致")
    status: Final = subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"], text=True
    )
    if status:
        raise SystemExit("源码目录有改动或未跟踪文件，请提供干净副本")
    subprocess.run(["git", "-C", str(source), "apply", "--check", "--whitespace=error", str(patch)], check=True)
    subprocess.run(["git", "-C", str(source), "apply", "--whitespace=error", str(patch)], check=True)
    subprocess.run(["git", "-C", str(source), "diff", "--check"], check=True)
    sys.stdout.write(f"Applied {patch.name} to {source} at {head}\n")


if __name__ == "__main__":
    main()
