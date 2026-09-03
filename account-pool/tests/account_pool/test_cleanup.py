"""验证删除清理进度构造保持单向、不可变的检查点语义。"""

from account_pool.cleanup import compose_removed, directory_removed, routes_removed
from account_pool.domain import CleanupProgress


def test_cleanup_progress_builders_advance_steps_without_mutating_input() -> None:
    initial = CleanupProgress()

    routed = routes_removed(initial)
    composed = compose_removed(routed)
    completed = directory_removed()

    assert initial == CleanupProgress()
    assert routed.routes_removed is True
    assert routed.compose_removed is False
    assert composed.compose_removed is True
    assert composed.directory_removed is False
    assert completed.complete is True
