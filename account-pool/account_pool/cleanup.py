"""本模块封装号池删除流程使用的清理进度构造，不持有锁或执行持久化。"""

from account_pool.domain import CleanupProgress


def routes_removed(progress: CleanupProgress) -> CleanupProgress:
    return CleanupProgress(
        routes_removed=True,
        compose_removed=progress.compose_removed,
        directory_removed=progress.directory_removed,
    )


def compose_removed(progress: CleanupProgress) -> CleanupProgress:
    return CleanupProgress(
        routes_removed=True,
        compose_removed=True,
        directory_removed=progress.directory_removed,
    )


def directory_removed() -> CleanupProgress:
    return CleanupProgress(routes_removed=True, compose_removed=True, directory_removed=True)
