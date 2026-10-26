import importlib
import os
from pathlib import Path

import pytest
import yaml

HERE = Path(os.environ.get("COCKPIT_TEST_SOURCE", Path(__file__).parent))
PACKAGE = "ghcr.io/vvv-345/litellm-cockpit"
LITELLM = "sha256:" + "1" * 64
COCKPIT = "sha256:" + "2" * 64


def load_publisher(monkeypatch):
    assert (HERE / "image_tags.py").exists(), "semantic image tag publisher is missing"
    monkeypatch.syspath_prepend(str(HERE))
    return importlib.import_module("image_tags")


class Registry:
    def __init__(self, existing=None, error=None, corrupt=False):
        self.tags = dict(existing or {})
        self.error = error
        self.corrupt = corrupt
        self.writes = []

    def __call__(self, command):
        assert command[:3] == ("docker", "buildx", "imagetools")
        if command[3] == "inspect":
            assert command[5:] == ("--format", "{{.Manifest.Digest}}")
            if self.error:
                raise RuntimeError(self.error)
            if command[4] not in self.tags:
                raise RuntimeError(f"ERROR: {command[4]}: not found")
            return self.tags[command[4]] + "\n"
        assert command[3:6] == ("create", "--prefer-index=false", "--tag")
        tag, source = command[6:]
        assert source.startswith(PACKAGE + "@sha256:")
        self.writes.append((tag, source))
        self.tags[tag] = "sha256:" + "3" * 64 if self.corrupt else source.split("@")[1]
        return ""


def release(publisher):
    return publisher.Release(
        1,
        "cockpit-test",
        "a" * 10,
        "a" * 40,
        "b" * 40,
        "c" * 64,
        "d" * 64,
        PACKAGE + "@" + LITELLM,
        PACKAGE + "@" + COCKPIT,
    )


def test_publish_pair_preserves_digests_and_is_idempotent(monkeypatch):
    publisher = load_publisher(monkeypatch)
    registry = Registry()
    publisher.publish_image_tags(release(publisher), "v1.0.1", registry)
    publisher.publish_image_tags(release(publisher), "v1.0.1", registry)
    assert registry.writes == [
        (PACKAGE + ":litellm-v1.0.1", PACKAGE + "@" + LITELLM),
        (PACKAGE + ":cockpit-v1.0.1", PACKAGE + "@" + COCKPIT),
    ]


def test_conflict_on_second_component_prevents_all_writes(monkeypatch):
    publisher = load_publisher(monkeypatch)
    registry = Registry({PACKAGE + ":cockpit-v1.0": LITELLM})
    with pytest.raises(RuntimeError, match="already points"):
        publisher.publish_image_tags(release(publisher), "v1.0", registry)
    assert registry.writes == []


@pytest.mark.parametrize(
    "error", ["401 Unauthorized", "403 denied", "read operation timed out", "credential helper not found"]
)
def test_lookup_failure_is_not_treated_as_missing(monkeypatch, error):
    publisher = load_publisher(monkeypatch)
    registry = Registry(error=error)
    with pytest.raises(RuntimeError, match=error):
        publisher.publish_image_tags(release(publisher), "v2.0", registry)
    assert registry.writes == []


def test_mismatched_readback_fails_release(monkeypatch):
    publisher = load_publisher(monkeypatch)
    with pytest.raises(RuntimeError, match="digest mismatch"):
        publisher.publish_image_tags(release(publisher), "v1.2", Registry(corrupt=True))


def test_partial_pair_can_resume_without_overwriting(monkeypatch):
    publisher = load_publisher(monkeypatch)
    registry = Registry({PACKAGE + ":litellm-v1.3": LITELLM})
    publisher.publish_image_tags(release(publisher), "v1.3", registry)
    assert registry.writes == [(PACKAGE + ":cockpit-v1.3", PACKAGE + "@" + COCKPIT)]


@pytest.mark.parametrize("version", ["1.0", "latest", "v1.0; echo bad"])
def test_invalid_version_never_writes(monkeypatch, version):
    publisher = load_publisher(monkeypatch)
    registry = Registry()
    with pytest.raises(ValueError, match="invalid release version"):
        publisher.publish_image_tags(release(publisher), version, registry)
    assert registry.writes == []


@pytest.mark.parametrize(
    ("base", "tags", "expected"),
    [
        ("v1.2", ("cockpit-v1.1.9",), "v1.2"),
        ("v1.3", ("cockpit-v1.2.9",), "v1.3"),
        ("v1.2", ("cockpit-v1.2",), "v1.2.1"),
        ("v2.0", ("cockpit-v2.0",), "v2.0.1"),
    ],
)
def test_user_series_and_subsequent_patch_are_not_hardcoded(monkeypatch, base, tags, expected):
    monkeypatch.syspath_prepend(str(HERE))
    versions = importlib.import_module("release_version")
    assert versions.next_version(base, tags, ()) == expected


def test_workflow_records_success_only_after_smoke_and_image_aliases():
    jobs = yaml.safe_load((HERE.parents[1] / ".github/workflows/publish-cockpit-images.yml").read_text())["jobs"]
    assert "smoke" in jobs["publish"]["needs"]
    steps = jobs["publish"]["steps"]
    alias_steps = [step for step in steps if "image_tags.py" in step.get("run", "")]
    assert len(alias_steps) == 1, "successful releases do not publish semantic GHCR tags"
    assert alias_steps[0].get("continue-on-error", False) is False
    assert alias_steps[0]["env"]["RELEASE_VERSION"] == "${{ needs.prepare.outputs.release_version }}"
    assert "publish" in jobs["tag-version"]["needs"]
    assert jobs["tag-version"].get("if", "success()") == "success()"
