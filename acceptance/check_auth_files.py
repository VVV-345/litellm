"""只读校验外部 Codex JSON，复用生产凭证校验，不输出账号、文件名和认证内容。"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path
from typing import Final, Literal, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "account-pool"))

from account_pool.credential_ownership import CredentialConflict
from account_pool.onboarding_models import OnboardingEntry
from account_pool.onboarding_service import file_identity
from account_pool.secrets import EnvironmentSecretDeriver


class FileResult(TypedDict):
    slot: int
    status: Literal["passed", "failed"]
    unchanged: bool
    reason: str


class Inspection(TypedDict):
    status: Literal["passed", "failed"]
    files: list[FileResult]


def inspect_file(path: Path, slot: int, deriver: EnvironmentSecretDeriver) -> FileResult:
    before: Final = path.read_bytes()
    try:
        entry: Final = OnboardingEntry(label=f"slot-{slot}.json", content=before.decode("utf-8-sig"))
        file_identity(entry, "openai_codex", deriver)
    except (ValueError, CredentialConflict):
        return FileResult(slot=slot, status="failed", unchanged=path.read_bytes() == before,
                          reason="JSON、供应商或凭证身份不符合生产导入契约")
    unchanged: Final = hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(before).digest()
    return FileResult(slot=slot, status="passed" if unchanged else "failed", unchanged=unchanged,
                      reason="生产文件校验通过；未验证上游可用性")


def inspect_directory(directory: Path) -> Inspection:
    deriver: Final = EnvironmentSecretDeriver(secrets.token_hex(32))
    files: Final = [inspect_file(path, slot, deriver) for slot, path in enumerate(sorted(directory.glob("*.json")), 1)]
    return Inspection(status="passed" if files and all(item["status"] == "passed" for item in files) else "failed",
                      files=files)


def main() -> int:
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args: Final = parser.parse_args()
    result: Final = inspect_directory(args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
