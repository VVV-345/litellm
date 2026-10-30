"""Create the three-component release manifest from verified build receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Final, Protocol, cast

from release_platforms import parse_architectures

REPOSITORY: Final = "ghcr.io/vvv-345/litellm-cliproxyapi"
COMPONENTS: Final = ("litellm", "manager", "cliproxyapi")
ADAPTER: Final = Path(__file__).resolve().parents[1] / "account-pool/cliproxy-adapter"


class Arguments(Protocol):
    architectures: str
    revision: str
    version: str
    digests: Path
    output: Path


def read_object(path: Path) -> dict[str, object]:
    value: Final[object] = json.loads(path.read_bytes())
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path.name}: expected JSON object")
    return cast(dict[str, object], value)


def checked(value: object, pattern: str, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError(f"invalid {field}")
    return value


def platform_digest(root: Path, component: str, arch: str, revision: str) -> str:
    receipt: Final = read_object(root / f"{component}-{arch}.json")
    if (receipt.get("image"), receipt.get("arch"), receipt.get("revision"), receipt.get("probe")) != (
        component, arch, revision, "passed"
    ):
        raise ValueError(f"{component}/{arch}: missing successful probe or mismatched identity")
    return checked(receipt.get("digest"), r"sha256:[0-9a-f]{64}", f"{component}/{arch} digest")


def adapter_provenance() -> dict[str, object]:
    sources: Final = read_object(ADAPTER / "sources.json")
    upstream: Final = sources.get("cliproxyapi")
    patch: Final = sources.get("patch")
    if not isinstance(upstream, dict) or not isinstance(patch, dict):
        raise ValueError("invalid adapter pin")
    commit: Final = checked(upstream.get("commit"), r"[0-9a-f]{40}", "adapter source revision")
    patch_hash: Final = checked(patch.get("sha256"), r"[0-9a-f]{64}", "adapter patch hash")
    patch_file: Final = checked(patch.get("file"), r"[a-zA-Z0-9_.-]+\.patch", "adapter patch file")
    if hashlib.sha256((ADAPTER / patch_file).read_bytes()).hexdigest() != patch_hash:
        raise ValueError("adapter patch hash mismatch")
    return {"source_revision": commit, "patch_sha256": patch_hash, "managed_identity_version": 1}


def component_manifest(args: Arguments, component: str, architectures: tuple[str, ...]) -> dict[str, object]:
    receipt: Final = read_object(args.digests / f"{component}.json")
    if receipt.get("image") != component or receipt.get("platforms") != [f"linux/{arch}" for arch in architectures]:
        raise ValueError(f"{component}: incorrect component or platforms")
    digest: Final = checked(receipt.get("digest"), r"sha256:[0-9a-f]{64}", f"{component} manifest digest")
    return {
        "image": f"{REPOSITORY}:{component}-{args.version}-{args.revision[:10]}",
        "digest": digest,
        "platforms": receipt["platforms"],
        "platform_digests": {
            f"linux/{arch}": platform_digest(args.digests, component, arch, args.revision)
            for arch in architectures
        },
        **(adapter_provenance() if component == "cliproxyapi" else {"source_revision": args.revision}),
    }


def main() -> int:
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--architectures", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--digests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args: Final = cast(Arguments, parser.parse_args())
    try:
        architectures: Final = parse_architectures(args.architectures)
        checked(args.revision, r"[0-9a-f]{40}", "product revision")
        checked(args.version, r"v[0-9]+\.[0-9]+(?:\.[0-9]+)?", "product version")
        manifest: Final = {
            "schema": 2,
            "repository": REPOSITORY,
            "revision": args.revision,
            "version": args.version,
            "deploy_tag": args.revision[:10],
            "versioned_tag": f"{args.version}-{args.revision[:10]}",
            "images": {component: component_manifest(args, component, architectures) for component in COMPONENTS},
        }
        args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        sys.stderr.write(f"Release manifest rejected: {error}\n")
        return 2
    sys.stdout.write(f"Release manifest: {args.output.resolve()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
