"""应用日志页面的进程输出设置，复用 LiteLLM 格式和脱敏，不影响费用记录。"""

import copy
import logging
import os
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Full, Queue
from typing import Final

from litellm import _logging
from litellm.proxy.management_endpoints.account_pool_full_logs import full_log_store
from litellm.proxy.management_endpoints.account_pool_management_models import AccountPoolSettings


class RuntimeFormatter(logging.Formatter):
    def __init__(self, delegate: logging.Formatter, stacktrace: bool) -> None:
        super().__init__()
        self.delegate: Final = delegate
        self.stacktrace: Final = stacktrace

    def format(self, record: logging.LogRecord) -> str:
        cleaned: Final = copy.copy(record)
        if not self.stacktrace:
            cleaned.exc_info = None
            cleaned.exc_text = None
            cleaned.stack_info = None
        return _logging.redact_secrets(self.delegate.format(cleaned))


class RuntimeQueueHandler(QueueHandler):
    def __init__(self, queue: Queue[logging.LogRecord | None]) -> None:
        super().__init__(queue)
        self.dropped = 0

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except Full:
            self.dropped += 1


class RuntimeQueueListener(QueueListener):
    def __init__(self, queue: Queue[logging.LogRecord | None], handler: logging.Handler) -> None:
        super().__init__(queue, handler, respect_handler_level=True)
        self.pending: Final = queue

    def enqueue_sentinel(self) -> None:
        self.pending.put(None)


class RuntimeLogging:
    def __init__(self) -> None:
        self.loggers: Final = (_logging.verbose_logger, _logging.verbose_proxy_logger, _logging.verbose_router_logger)
        self.original_propagation: Final = tuple(logger.propagate for logger in self.loggers)
        self.original_levels: Final = tuple(logger.level for logger in self.loggers)
        self.original_formatter: Final = _logging.handler.formatter or logging.Formatter("%(message)s")
        self.original_handler_level: Final = _logging.handler.level
        self.applied: tuple[object, ...] | None = None
        self.file_handler: RotatingFileHandler | None = None
        self.queue_handler: RuntimeQueueHandler | None = None
        self.listener: QueueListener | None = None
        self.dependency_levels: Final = {
            name: logging.getLogger(name).level for name in ("httpx", "httpcore", "apscheduler")
        }

    def apply(self, settings: AccountPoolSettings) -> None:
        fingerprint: Final = (
            settings.runtime_log_level,
            settings.runtime_log_format,
            settings.runtime_log_console,
            settings.runtime_log_file,
            settings.runtime_log_max_mb,
            settings.runtime_log_backups,
            settings.runtime_log_stacktrace,
            settings.runtime_log_quiet_dependencies,
        )
        if fingerprint == self.applied:
            return
        formatter: Final = RuntimeFormatter(
            _logging.JsonFormatter()
            if settings.runtime_log_format == "json"
            else _logging.CorrelationPlainFormatter("%(asctime)s [%(levelname)s] pid:%(process)d %(name)s %(message)s")
            if settings.runtime_log_format == "text"
            else self.original_formatter,
            settings.runtime_log_stacktrace,
        )
        level: Final = (
            self.original_handler_level
            if settings.runtime_log_level == "inherit"
            else logging.getLevelNamesMapping()[settings.runtime_log_level]
        )
        # 先准备新文件处理器，失败时保留当前输出，日志设置不能使请求服务退出。
        new_file: Final = self._file(settings, formatter, level) if settings.runtime_log_file else None
        queue: Final[Queue[logging.LogRecord | None]] = Queue(maxsize=10000)
        queued: Final = RuntimeQueueHandler(queue) if new_file is not None else None
        listener: Final = RuntimeQueueListener(queue, new_file) if new_file is not None else None
        if queued is not None:
            queued.setLevel(level)
        if listener is not None:
            listener.start()
        for logger, original, propagate in zip(self.loggers, self.original_levels, self.original_propagation):
            logger.propagate = propagate if settings.runtime_log_console else False
            logger.setLevel(original if settings.runtime_log_level == "inherit" else level)
            if self.queue_handler is not None:
                logger.removeHandler(self.queue_handler)
            if queued is not None:
                logger.addHandler(queued)
            if settings.runtime_log_console and _logging.handler not in logger.handlers:
                logger.addHandler(_logging.handler)
            if not settings.runtime_log_console:
                logger.removeHandler(_logging.handler)
        _logging.handler.setFormatter(formatter)
        _logging.handler.setLevel(level)
        if self.listener is not None:
            self.listener.stop()
        if self.file_handler is not None:
            self.file_handler.close()
        for name, original in self.dependency_levels.items():
            logging.getLogger(name).setLevel(logging.WARNING if settings.runtime_log_quiet_dependencies else original)
        self.file_handler = new_file
        self.queue_handler = queued
        self.listener = listener
        self.applied = fingerprint

    @staticmethod
    def _file(settings: AccountPoolSettings, formatter: logging.Formatter, level: int) -> RotatingFileHandler:
        path: Final = full_log_store().path.parent / "runtime"
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination: Final[Path] = path / f"litellm-{os.getpid()}.log"
        handler: Final = RotatingFileHandler(
            destination,
            maxBytes=settings.runtime_log_max_mb * 1024 * 1024,
            backupCount=settings.runtime_log_backups,
            encoding="utf-8",
        )
        destination.chmod(0o600)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        return handler


runtime_logging: Final = RuntimeLogging()
