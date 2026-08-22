"""导出脱敏系统运行事件的领域契约与 PostgreSQL 实现。"""

from account_pool.operational.models import (
    OperationalEventFact,
    OperationalEventOutcome,
    OperationalEventRecord,
    OperationalEventSource,
    OperationalEventType,
    OperationalPoolEvent,
    ParserSnapshotExportTrigger,
    ParserTaskInterruptionSource,
    build_parser_snapshot_export_record,
    build_parser_task_operational_record,
    build_sync_reconcile_record,
)
from account_pool.operational.postgres import PostgresOperationalEventRepository
from account_pool.operational.repository import (
    OperationalEventRepository,
    OperationalPersistenceFailure,
    OperationalPersistenceFailureCode,
    OperationalWriteResult,
    OperationalWriteSuccess,
)

__all__ = [
    "OperationalEventFact",
    "OperationalEventOutcome",
    "OperationalEventRecord",
    "OperationalEventRepository",
    "OperationalEventSource",
    "OperationalEventType",
    "OperationalPersistenceFailure",
    "OperationalPersistenceFailureCode",
    "OperationalPoolEvent",
    "OperationalWriteResult",
    "OperationalWriteSuccess",
    "ParserSnapshotExportTrigger",
    "ParserTaskInterruptionSource",
    "PostgresOperationalEventRepository",
    "build_parser_snapshot_export_record",
    "build_parser_task_operational_record",
    "build_sync_reconcile_record",
]
