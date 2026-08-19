"""生成并原子保存解析器的版本化脱敏 JSON 快照。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, TypeAdapter, ValidationError

from account_pool.models import FrozenModel
from account_pool.parsing.models import ParsedChannelData, ParserIssue, ParserRun, ParserRunStatus
from account_pool.parsing.safety import has_safe_parser_content


class ParserSnapshot(FrozenModel):
    schema_version: Literal[1] = 1
    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    parser_run_id: UUID
    parsed_at: AwareDatetime
    status: ParserRunStatus
    raw_result: ParsedChannelData
    effective_result: ParsedChannelData
    discovered_models: tuple[str, ...] = ()
    issues: tuple[ParserIssue, ...] = ()


class SnapshotExportFailureCode(StrEnum):
    UNSAFE_CONTENT = "unsafe_content"
    INVALID_LATEST = "invalid_latest"
    LATEST_READ_FAILED = "latest_read_failed"
    HISTORY_WRITE_FAILED = "history_write_failed"
    LATEST_WRITE_FAILED = "latest_write_failed"


class SnapshotExportSuccess(FrozenModel):
    status: Literal["written"] = "written"
    snapshot: ParserSnapshot


class SnapshotExportFailure(FrozenModel):
    status: Literal["failed"] = "failed"
    code: SnapshotExportFailureCode
    retryable: bool
    history_written: bool = False


SnapshotExportResult = SnapshotExportSuccess | SnapshotExportFailure
SnapshotWriter = Callable[[Path, bytes], bool]

DEFAULT_PARSER_SNAPSHOT_ROOT: Final = Path(__file__).resolve().parents[2] / "data" / "parser-snapshots"
_LATEST_ADAPTER: Final = TypeAdapter(dict[UUID, ParserSnapshot])


def project_parser_snapshot(
    run: ParserRun,
    effective_result: ParsedChannelData | None = None,
) -> ParserSnapshot:
    resolved_effective: Final = run.result if effective_result is None else effective_result
    return ParserSnapshot(
        parser_id=run.parser_id,
        parser_version=run.parser_version,
        parser_run_id=run.parser_run_id,
        parsed_at=run.parsed_at,
        status=run.status,
        raw_result=run.result,
        effective_result=resolved_effective,
        discovered_models=run.discovered_models,
        issues=run.issues,
    )


def _has_safe_content(snapshot: ParserSnapshot) -> bool:
    return has_safe_parser_content(snapshot.model_dump_json())


def _write_atomic(path: Path, payload: bytes) -> bool:
    temporary: Final = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        temporary.replace(path)
        return True
    except OSError:
        return False
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


class ParserSnapshotStore:
    def __init__(
        self,
        root: Path = DEFAULT_PARSER_SNAPSHOT_ROOT,
        write_atomic: SnapshotWriter = _write_atomic,
    ) -> None:
        self._root = root
        self._write_atomic = write_atomic

    def export(
        self,
        run: ParserRun,
        effective_result: ParsedChannelData | None = None,
    ) -> SnapshotExportResult:
        snapshot: Final = project_parser_snapshot(run=run, effective_result=effective_result)
        if not _has_safe_content(snapshot):
            return SnapshotExportFailure(
                code=SnapshotExportFailureCode.UNSAFE_CONTENT,
                retryable=False,
            )
        latest: Final = self._load_latest()
        if isinstance(latest, SnapshotExportFailure):
            return latest
        history_path: Final = self._history_path(channel_id=run.channel_id, parser_run_id=run.parser_run_id)
        history_payload: Final = snapshot.model_dump_json(indent=2).encode("utf-8")
        if not self._write_atomic(history_path, history_payload):
            return SnapshotExportFailure(
                code=SnapshotExportFailureCode.HISTORY_WRITE_FAILED,
                retryable=True,
            )
        current: Final = latest.get(run.channel_id)
        incoming_order: Final = (snapshot.parsed_at, str(snapshot.parser_run_id))
        current_order: Final = None if current is None else (current.parsed_at, str(current.parser_run_id))
        # 旧任务重试仍补写 history，但不得让渠道 latest 回退到更早的解析结果。
        if current_order is not None and incoming_order < current_order:
            return SnapshotExportSuccess(snapshot=snapshot)
        updated_latest: Final = {**latest, run.channel_id: snapshot}
        latest_payload: Final = _LATEST_ADAPTER.dump_json(updated_latest, indent=2)
        if not self._write_atomic(self._root / "latest.json", latest_payload):
            return SnapshotExportFailure(
                code=SnapshotExportFailureCode.LATEST_WRITE_FAILED,
                retryable=True,
                history_written=True,
            )
        return SnapshotExportSuccess(snapshot=snapshot)

    def _load_latest(self) -> dict[UUID, ParserSnapshot] | SnapshotExportFailure:
        latest_path: Final = self._root / "latest.json"
        if not latest_path.exists():
            return {}
        try:
            payload: Final = latest_path.read_bytes()
            snapshots: Final = _LATEST_ADAPTER.validate_json(payload)
            if any(not _has_safe_content(snapshot) for snapshot in snapshots.values()):
                return SnapshotExportFailure(
                    code=SnapshotExportFailureCode.UNSAFE_CONTENT,
                    retryable=False,
                )
            return snapshots
        except OSError:
            return SnapshotExportFailure(
                code=SnapshotExportFailureCode.LATEST_READ_FAILED,
                retryable=True,
            )
        except ValidationError:
            return SnapshotExportFailure(
                code=SnapshotExportFailureCode.INVALID_LATEST,
                retryable=False,
            )

    def _history_path(self, channel_id: UUID, parser_run_id: UUID) -> Path:
        return self._root / "history" / str(channel_id) / f"{parser_run_id}.json"
