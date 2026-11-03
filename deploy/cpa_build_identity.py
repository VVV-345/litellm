"""以固定来源、构建脚本、基础镜像和架构生成独立 CPA 指纹，不含整站版本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Final

from release_manifest import ADAPTER, adapter_provenance, checked
from release_platforms import parse_architectures


class Arguments(argparse.Namespace):
    builder: str
    runtime: str
    architectures: str


def build_key(builder: str, runtime: str, arch: str) -> str:
    checked(builder, r"golang@sha256:[0-9a-f]{64}", "CPA builder")
    checked(runtime, r"debian@sha256:[0-9a-f]{64}", "CPA runtime")
    parse_architectures(arch)
    payload: Final = MappingProxyType({
        "schema": 1, "platform": "linux/" + arch, "builder": builder, "runtime": runtime,
        **adapter_provenance(),
        "scripts": MappingProxyType({
            name: hashlib.sha256((ADAPTER / name).read_bytes()).hexdigest()
            for name in ("Dockerfile", "apply.py", "image_ref.py", "sources.json")
        }),
        "adapter_dependencies": hashlib.sha256((ADAPTER.parent / "uv.lock").read_bytes()).hexdigest(),
        "identity_script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=dict).encode()).hexdigest()


def main() -> None:
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--builder", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--architectures", required=True)
    args: Final = Arguments()
    parser.parse_args(namespace=args)
    keys: Final = MappingProxyType({arch: build_key(args.builder, args.runtime, arch)
                                  for arch in parse_architectures(args.architectures)})
    sys.stdout.write("build_keys=" + json.dumps(keys, sort_keys=True, separators=(",", ":"), default=dict) + "\n")


if __name__ == "__main__":
    main()
