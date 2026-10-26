import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def versions():
    path = HERE / "release_version.py"
    spec = importlib.util.spec_from_file_location("cockpit_release_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("version", "tags", "head_tags", "expected"),
    [
        ("v1.0", (), (), "v1.0"),
        ("v1.0", ("cockpit-v1.0",), (), "v1.0.1"),
        ("v1.0", ("cockpit-v1.0", "cockpit-v1.0.1"), (), "v1.0.2"),
        ("v1.0", ("cockpit-v1.0.5",), (), "v1.0.6"),
        ("v1.1", ("cockpit-v1.0", "cockpit-v1.0.5"), (), "v1.1"),
        ("v2.0", ("cockpit-v1.7.1",), (), "v2.0"),
        ("v1.0", ("cockpit-v1.0",), ("cockpit-v1.0",), "v1.0"),
        ("v1.0", ("cockpit-v1.0", "cockpit-v1.0.1"), ("cockpit-v1.0.1",), "v1.0.1"),
        ("v1.0", ("v1.0", "v1.1", "v1.1.1", "v1.7.1-a124c53814"), (), "v1.0"),
        ("v1.0", ("cockpit-test-571e5b7ff0", "v1.6.1-80056b807a", "v1.94.0-dev.2"), (), "v1.0"),
    ],
)
def test_next_version_defaults_to_patch_and_honours_explicit_series(versions, version, tags, head_tags, expected):
    assert versions.next_version(version, tags, head_tags) == expected


def test_invalid_version_fails_fast(versions):
    with pytest.raises(ValueError):
        versions.next_version("1.0", (), ())


def test_cli_emits_release_version(versions, tmp_path, monkeypatch, capsys):
    version = tmp_path / "VERSION"
    version.write_text("v1.0\n", encoding="utf-8")
    tags = tmp_path / "tags.txt"
    tags.write_text("cockpit-v1.0\ncockpit-v1.0.1\nv1.1\n", encoding="utf-8")
    head = tmp_path / "head.txt"
    head.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_version.py",
            str(version),
            "--tags-file",
            str(tags),
            "--head-tags-file",
            str(head),
        ],
    )

    assert versions.main() == 0
    assert capsys.readouterr().out == "release_version=v1.0.2\n"
