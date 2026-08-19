from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID

from account_pool.domain.provider_source import ProviderCapability
from account_pool.parsing.models import ParsedChannelData, ParserRun, ParserRunStatus, UnresolvedField
from account_pool.parsing.snapshots import (
    ParserSnapshot,
    ParserSnapshotStore,
    SnapshotExportFailure,
    SnapshotExportFailureCode,
    SnapshotExportSuccess,
    project_parser_snapshot,
)
from pydantic import TypeAdapter

_CHANNEL_ID: Final = UUID("10000000-0000-0000-0000-000000000001")
_RUN_ID: Final = UUID("20000000-0000-0000-0000-000000000002")
_LATEST_ADAPTER: Final = TypeAdapter(dict[UUID, ParserSnapshot])


def _run(channel_id: UUID = _CHANNEL_ID, parser_run_id: UUID = _RUN_ID) -> ParserRun:
    result: Final = ParsedChannelData(
        capabilities=(ProviderCapability.MODEL_DISCOVERY,),
        unresolved_fields=(
            UnresolvedField(path="subscription", reason="通用协议没有套餐接口"),
            UnresolvedField(path="metered", reason="通用协议没有价格接口"),
        ),
        warnings=("需要厂商专用解析器",),
    )
    return ParserRun(
        parser_run_id=parser_run_id,
        channel_id=channel_id,
        parser_id="openai-compatible",
        parser_version="1.0.0",
        parsed_at=datetime(2026, 8, 19, 8, 0, tzinfo=UTC),
        status=ParserRunStatus.PARTIAL,
        result=result,
        discovered_models=("model-a", "model-b"),
    )


def _reject_all_writes(_: Path, __: bytes) -> bool:
    return False


def _reject_latest_write(path: Path, payload: bytes) -> bool:
    if path.name == "latest.json":
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return True


def test_export_writes_versioned_latest_and_history_snapshots(tmp_path: Path) -> None:
    root: Final = tmp_path / "parser-snapshots"
    run: Final = _run()
    effective: Final = run.result.model_copy(update={"warnings": ("管理员已确认当前字段",)})

    exported: Final = ParserSnapshotStore(root).export(run=run, effective_result=effective)

    assert isinstance(exported, SnapshotExportSuccess)
    history_path: Final = root / "history" / str(run.channel_id) / f"{run.parser_run_id}.json"
    history: Final = ParserSnapshot.model_validate_json(history_path.read_bytes())
    latest: Final = _LATEST_ADAPTER.validate_json((root / "latest.json").read_bytes())
    assert history.schema_version == 1
    assert history.raw_result.warnings == ("需要厂商专用解析器",)
    assert history.effective_result.warnings == ("管理员已确认当前字段",)
    assert history.discovered_models == ("model-a", "model-b")
    assert latest[run.channel_id] == history
    assert frozenset(history.raw_result.model_dump()) == frozenset(
        ("subscription", "metered", "billing_routes", "capabilities", "unresolved_fields", "evidence", "warnings")
    )
    assert not tuple(root.rglob("*.tmp"))


def test_export_preserves_other_channels_in_latest(tmp_path: Path) -> None:
    root: Final = tmp_path / "parser-snapshots"
    first: Final = _run()
    second: Final = _run(
        channel_id=UUID("10000000-0000-0000-0000-000000000003"),
        parser_run_id=UUID("20000000-0000-0000-0000-000000000004"),
    )
    store: Final = ParserSnapshotStore(root)

    assert isinstance(store.export(first), SnapshotExportSuccess)
    assert isinstance(store.export(second), SnapshotExportSuccess)

    latest: Final = _LATEST_ADAPTER.validate_json((root / "latest.json").read_bytes())
    assert frozenset(latest) == frozenset((first.channel_id, second.channel_id))


def test_invalid_latest_is_not_overwritten(tmp_path: Path) -> None:
    root: Final = tmp_path / "parser-snapshots"
    root.mkdir()
    latest_path: Final = root / "latest.json"
    latest_path.write_text("not-json", encoding="utf-8")

    exported: Final = ParserSnapshotStore(root).export(_run())

    assert isinstance(exported, SnapshotExportFailure)
    assert exported.code == SnapshotExportFailureCode.INVALID_LATEST
    assert latest_path.read_text(encoding="utf-8") == "not-json"
    assert not (root / "history").exists()


def test_latest_read_failure_is_retryable(tmp_path: Path) -> None:
    root: Final = tmp_path / "parser-snapshots"
    (root / "latest.json").mkdir(parents=True)

    exported: Final = ParserSnapshotStore(root).export(_run())

    assert isinstance(exported, SnapshotExportFailure)
    assert exported.code == SnapshotExportFailureCode.LATEST_READ_FAILED
    assert exported.retryable is True
    assert not (root / "history").exists()


def test_sensitive_content_is_rejected_before_writing(tmp_path: Path) -> None:
    run: Final = _run().model_copy(
        update={"result": _run().result.model_copy(update={"warnings": ("authorization: bearer test-placeholder",)})}
    )
    root: Final = tmp_path / "parser-snapshots"

    exported: Final = ParserSnapshotStore(root).export(run)

    assert isinstance(exported, SnapshotExportFailure)
    assert exported.code == SnapshotExportFailureCode.UNSAFE_CONTENT
    assert not root.exists()


def test_existing_sensitive_latest_is_not_propagated(tmp_path: Path) -> None:
    root: Final = tmp_path / "parser-snapshots"
    root.mkdir()
    run: Final = _run()
    unsafe_result: Final = run.result.model_copy(update={"warnings": ("https://private.example",)})
    unsafe_snapshot: Final = project_parser_snapshot(run).model_copy(update={"raw_result": unsafe_result})
    original: Final = _LATEST_ADAPTER.dump_json({run.channel_id: unsafe_snapshot}, indent=2)
    latest_path: Final = root / "latest.json"
    latest_path.write_bytes(original)

    exported: Final = ParserSnapshotStore(root).export(run)

    assert isinstance(exported, SnapshotExportFailure)
    assert exported.code == SnapshotExportFailureCode.UNSAFE_CONTENT
    assert latest_path.read_bytes() == original
    assert not (root / "history").exists()


def test_write_failures_are_returned_for_worker_retry(tmp_path: Path) -> None:
    history_failure: Final = ParserSnapshotStore(
        tmp_path / "history-failure",
        write_atomic=_reject_all_writes,
    ).export(_run())
    latest_failure: Final = ParserSnapshotStore(
        tmp_path / "latest-failure",
        write_atomic=_reject_latest_write,
    ).export(_run())

    assert isinstance(history_failure, SnapshotExportFailure)
    assert history_failure.code == SnapshotExportFailureCode.HISTORY_WRITE_FAILED
    assert history_failure.retryable is True
    assert history_failure.history_written is False
    assert isinstance(latest_failure, SnapshotExportFailure)
    assert latest_failure.code == SnapshotExportFailureCode.LATEST_WRITE_FAILED
    assert latest_failure.retryable is True
    assert latest_failure.history_written is True
    assert tuple((tmp_path / "latest-failure" / "history").rglob("*.json"))
