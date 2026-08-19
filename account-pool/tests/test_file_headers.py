"""验证 Account Pool 手写文件均以中文用途说明开头。"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMENT_PREFIXES = {
    ".css": "/*",
    ".html": "<!--",
    ".js": "//",
    ".md": "<!--",
    ".py": '"""',
    ".toml": "#",
    ".yaml": "#",
    ".yml": "#",
}
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
    }
)
CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _source_files() -> tuple[Path, ...]:
    candidates = (
        path
        for path in PROJECT_ROOT.rglob("*")
        if path.is_file()
        and not EXCLUDED_DIRECTORY_NAMES.intersection(path.parts)
        and (path.suffix in COMMENT_PREFIXES or path.name == "Dockerfile")
    )
    return tuple(sorted(candidates))


@pytest.mark.parametrize("path", _source_files(), ids=lambda path: str(path.relative_to(PROJECT_ROOT)))
def test_handwritten_file_starts_with_chinese_purpose(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    expected_prefix = "#" if path.name == "Dockerfile" else COMMENT_PREFIXES[path.suffix]

    assert first_line.startswith(expected_prefix), f"{path} 缺少文件用途说明"
    assert CHINESE_TEXT_PATTERN.search(first_line), f"{path} 的文件用途说明需要使用中文"
