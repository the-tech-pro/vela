"""Durable, atomic journal for safety-sensitive iPod operations."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

JOURNAL_SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "blocked"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class OperationJournalError(RuntimeError):
    """Raised when a journal record is missing, invalid, or unsafe."""


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-compatible copy of journal metadata."""
    if depth >= 6:
        return str(value)[:2_000]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                break
            result[str(key)[:128]] = _bounded_json(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_bounded_json(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (int, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)[:2_000]
    return str(value)[:2_000]


class OperationJournal:
    """Persist one immutable-identity record per operation using atomic replace."""

    def __init__(
        self,
        directory: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = Path(directory)
        self.clock = clock
        self._lock = threading.RLock()

    def start(
        self,
        kind: str,
        *,
        operation_id: str = "",
        phase: str = "preparing",
        can_cancel: bool = True,
        target_id: str = "",
        source_id: str = "",
        target_archive_id: str = "",
        source_archive_id: str = "",
        snapshot_id: str = "",
        reconnect: Mapping[str, Any] | None = None,
        recovery: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind = self._validate_token(kind, "operation kind")
        phase = self._validate_token(phase, "operation phase")
        operation_id = operation_id or f"op_{secrets.token_urlsafe(18)}"
        self._validate_id(operation_id, "operation")
        now = float(self.clock())
        record = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "revision": 1,
            "operation_id": operation_id,
            "kind": kind,
            "phase": phase,
            "can_cancel": bool(can_cancel),
            "target_id": str(target_id)[:256],
            "source_id": str(source_id)[:256],
            "target_archive_id": str(target_archive_id)[:128],
            "source_archive_id": str(source_archive_id)[:128],
            "snapshot_id": str(snapshot_id)[:128],
            "safety_snapshot_id": "",
            "reconnect": _bounded_json(reconnect or {}),
            "recovery": _bounded_json(recovery or {}),
            "status": "running",
            "error": {},
            "metadata": _bounded_json(metadata or {}),
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            path = self._path(operation_id)
            if path.exists():
                raise OperationJournalError("The operation ID is already present in the journal.")
            self._write(path, record)
        return _bounded_json(record)

    def transition(
        self,
        operation_id: str,
        phase: str,
        *,
        can_cancel: bool | None = None,
        safety_snapshot_id: str | None = None,
        reconnect: Mapping[str, Any] | None = None,
        recovery: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        phase = self._validate_token(phase, "operation phase")
        with self._lock:
            record = self._read(operation_id)
            if record["status"] in TERMINAL_STATUSES:
                raise OperationJournalError("A terminal operation cannot change phase.")
            record["phase"] = phase
            if can_cancel is not None:
                record["can_cancel"] = bool(can_cancel)
            if safety_snapshot_id is not None:
                record["safety_snapshot_id"] = str(safety_snapshot_id)[:128]
            if reconnect is not None:
                record["reconnect"] = _bounded_json(reconnect)
            if recovery is not None:
                record["recovery"] = _bounded_json(recovery)
            record["revision"] = int(record["revision"]) + 1
            record["updated_at"] = float(self.clock())
            self._write(self._path(operation_id), record)
            return _bounded_json(record)

    def finish(
        self,
        operation_id: str,
        status: str,
        *,
        phase: str = "complete",
        safety_snapshot_id: str | None = None,
        reconnect: Mapping[str, Any] | None = None,
        recovery: Mapping[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise OperationJournalError(f"Unsupported terminal operation status: {status}")
        phase = self._validate_token(phase, "operation phase")
        with self._lock:
            record = self._read(operation_id)
            if record["status"] in TERMINAL_STATUSES:
                if record["status"] != status:
                    raise OperationJournalError("A terminal operation status is immutable.")
                return _bounded_json(record)
            now = float(self.clock())
            record.update({
                "phase": phase,
                "can_cancel": False,
                "status": status,
                "revision": int(record["revision"]) + 1,
                "updated_at": now,
                "completed_at": now,
                "error": {
                    "code": str(error_code)[:128],
                    "message": str(error_message)[:2_000],
                } if error_code or error_message else {},
            })
            if safety_snapshot_id is not None:
                record["safety_snapshot_id"] = str(safety_snapshot_id)[:128]
            if reconnect is not None:
                record["reconnect"] = _bounded_json(reconnect)
            if recovery is not None:
                record["recovery"] = _bounded_json(recovery)
            self._write(self._path(operation_id), record)
            return _bounded_json(record)

    def get(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            return _bounded_json(self._read(operation_id))

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self.directory.exists():
                return None
            latest_record: dict[str, Any] | None = None
            latest_key = (-1.0, -1, -1.0, "")
            for path in self.directory.glob("*.json"):
                if not _ID_RE.fullmatch(path.stem):
                    continue
                try:
                    record = self._read(path.stem)
                except OperationJournalError:
                    continue
                try:
                    modified_ns = path.stat().st_mtime_ns
                except OSError:
                    modified_ns = 0
                key = (
                    float(record.get("updated_at", 0)),
                    modified_ns,
                    float(record.get("started_at", 0)),
                    str(record["operation_id"]),
                )
                if key > latest_key:
                    latest_key = key
                    latest_record = record
            return _bounded_json(latest_record) if latest_record is not None else None

    def recovery_state(self) -> dict[str, Any]:
        record = self.latest()
        if record is None:
            return {
                "journal_version": JOURNAL_SCHEMA_VERSION,
                "operation": None,
                "incomplete": False,
                "requires_recovery": False,
                "reconnect": {},
                "recovery": {},
            }
        incomplete = record["status"] == "running"
        recovery = record.get("recovery") if isinstance(record.get("recovery"), dict) else {}
        requires_recovery = bool(recovery.get("required"))
        if incomplete and record.get("phase") in {"committing", "finalizing", "recovery_required"}:
            requires_recovery = True
        return {
            "journal_version": JOURNAL_SCHEMA_VERSION,
            "operation": record,
            "incomplete": incomplete,
            "requires_recovery": requires_recovery,
            "reconnect": record.get("reconnect", {}),
            "recovery": recovery,
        }

    def _path(self, operation_id: str) -> Path:
        self._validate_id(operation_id, "operation")
        return self.directory / f"{operation_id}.json"

    def _read(self, operation_id: str) -> dict[str, Any]:
        path = self._path(operation_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OperationJournalError("The operation journal record was not found.") from exc
        if not isinstance(payload, dict):
            raise OperationJournalError("The operation journal record is invalid.")
        if (
            payload.get("schema_version") != JOURNAL_SCHEMA_VERSION
            or payload.get("operation_id") != operation_id
            or payload.get("status") not in {"running", *TERMINAL_STATUSES}
            or not isinstance(payload.get("revision"), int)
        ):
            raise OperationJournalError("The operation journal record is invalid.")
        self._validate_token(str(payload.get("kind", "")), "operation kind")
        self._validate_token(str(payload.get("phase", "")), "operation phase")
        return payload

    def _write(self, path: Path, record: Mapping[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(_bounded_json(record), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _fsync_directory(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not _ID_RE.fullmatch(value):
            raise OperationJournalError(f"The {label} ID is invalid.")
        return value

    @staticmethod
    def _validate_token(value: str, label: str) -> str:
        if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
            raise OperationJournalError(f"The {label} is invalid.")
        return value
