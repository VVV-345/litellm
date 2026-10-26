from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
from pathlib import Path
from typing import Final

from releasectl import PACKAGE, json_object, matched, parse_release, run, runtime_hash, save

COCKPIT_REVISION: Final = "ec9989c009042a3f8770781926f6087000825ee9"
ROOT: Final = Path(__file__).resolve().parents[2]


def compatibility_hash(root: Path, cockpit_revision: str) -> str:
    paths: Final = tuple(
        sorted(
            path
            for path in (
                *root.glob("schema.prisma"),
                *root.glob("litellm-proxy-extras/litellm_proxy_extras/schema.prisma"),
                *root.glob("litellm-proxy-extras/litellm_proxy_extras/migrations/**/*"),
                *root.glob("litellm/proxy/management_endpoints/account_pool_cockpit*.py"),
                *root.glob("litellm/proxy/management_endpoints/account_pool_native*.py"),
                *root.glob("deploy/cockpit/compose.yaml"),
            )
            if path.is_file()
        )
    )
    return hashlib.sha256(
        cockpit_revision.encode()
        + b"".join(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() for path in paths)
    ).hexdigest()


def prepare() -> None:
    revision: Final = matched(run(("git", "rev-parse", "HEAD")).strip(), "[0-9a-f]{40}")
    metadata: Final = {
        "format": 1,
        "channel": "cockpit-test",
        "version": revision[:10],
        "litellm_revision": revision,
        "cockpit_revision": COCKPIT_REVISION,
        "compatibility": compatibility_hash(ROOT, COCKPIT_REVISION),
        "runtime": runtime_hash(ROOT / "deploy/cockpit"),
    }
    save(Path("build-metadata.json"), metadata)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for key, value in metadata.items():
            print(f"{key}={value}", file=output)


def bundle() -> None:
    metadata: Final = json_object(Path("build-metadata.json").read_bytes())
    manifest: Final = {
        **metadata,
        "litellm": PACKAGE + "@" + matched(os.environ["LITELLM_DIGEST"], r"sha256:[0-9a-f]{64}"),
        "cockpit": PACKAGE + "@" + matched(os.environ["COCKPIT_DIGEST"], r"sha256:[0-9a-f]{64}"),
    }
    parse_release(json.dumps(manifest).encode(), matched(metadata["version"], "[0-9a-f]{10}"))
    save(Path("release.json"), manifest)
    with tarfile.open("runtime.tar.gz", "w:gz") as archive:
        for name in ("releasectl.py", "compose.yaml", "README.md"):
            archive.add(ROOT / "deploy/cockpit" / name, arcname=name)
    Path("SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(Path(name).read_bytes()).hexdigest()}  {name}\n"
            for name in ("runtime.tar.gz", "release.json")
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    {"prepare": prepare, "bundle": bundle}[sys.argv[1]]()
