from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

from release_version import version_parts
from releasectl import PACKAGE, Release, Runner, matched, read_release, run


def inspect_digest(tag: str, runner: Runner) -> str | None:
    try:
        return matched(
            runner(("docker", "buildx", "imagetools", "inspect", tag, "--format", "{{.Manifest.Digest}}")).strip(),
            r"sha256:[0-9a-f]{64}",
        )
    except RuntimeError as error:
        if f"{tag}: not found" in str(error) or "manifest unknown" in str(error).lower():
            return None
        raise


def publish_image_tags(release: Release, version: str, runner: Runner = run) -> None:
    version_parts(version)
    images: Final = (
        (f"{PACKAGE}:litellm-{version}", release.litellm),
        (f"{PACKAGE}:cockpit-{version}", release.cockpit),
    )
    existing: Final = tuple(inspect_digest(tag, runner) for tag, _source in images)
    for (tag, source), digest in zip(images, existing, strict=True):
        if digest is not None and source != f"{PACKAGE}@{digest}":
            raise RuntimeError(f"{tag} already points to {digest}; no overwrite allowed")
    for (tag, source), digest in zip(images, existing, strict=True):
        if digest is None:
            runner(("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", tag, source))
        observed: Final = inspect_digest(tag, runner)
        if source != f"{PACKAGE}@{observed}":
            raise RuntimeError(f"{tag} digest mismatch: expected {source}, observed {observed}")
        sys.stdout.write(f"{tag} -> {observed}\n")


if __name__ == "__main__":
    publish_image_tags(read_release(Path("release.json")), os.environ["RELEASE_VERSION"])
