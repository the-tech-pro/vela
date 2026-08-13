"""Versioned NDJSON command surface for :mod:`antra.core.ipod_service`."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .ipod_service import (
    IPodService,
    IPodServiceError,
    PROTOCOL_VERSION,
    _translate_write_safety_error,
)


def _emit(event_type: str, data: Any = None, **extra: Any) -> None:
    payload = {"type": event_type, "protocol_version": PROTOCOL_VERSION}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _load_request(path: str) -> dict[str, Any]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IPodServiceError("invalid_request", "The iPod request must be a JSON object.")
    version = int(payload.get("protocol_version", PROTOCOL_VERSION))
    if version != PROTOCOL_VERSION:
        raise IPodServiceError("protocol_mismatch", f"Unsupported iPod protocol version: {version}")
    return payload


def run_ipod_command(args: Any) -> int:
    request = _load_request(getattr(args, "ipod_request", ""))
    app_data = (
        request.get("app_data_dir")
        or getattr(args, "ipod_app_data", "")
        or (Path(getattr(args, "config", "")).resolve().parent if getattr(args, "config", "") else Path.cwd())
    )
    service = IPodService(app_data)
    operation = str(getattr(args, "ipod_operation", "") or request.get("operation", "")).strip()
    cancel_path = str(request.get("cancel_path") or getattr(args, "ipod_cancel_file", "") or "")
    cancelled = lambda: bool(cancel_path and os.path.exists(cancel_path))
    progress = lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)

    try:
        if operation == "scan":
            _emit("ipod_scan", service.scan())
        elif operation == "watch":
            service.watch(
                lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
                interval_seconds=float(request.get("interval_seconds", 2)),
                cancelled=cancelled,
            )
            _emit("ipod_watch_stopped", {"cancelled": cancelled()})
        elif operation == "browse":
            result = service.browse(
                str(request.get("mount_path", "")),
                str(request.get("resource", "tracks")),
                int(request.get("page", 1)),
                int(request.get("page_size", 100)),
            )
            _emit("ipod_browse", result)
        elif operation == "backup-devices":
            _emit("ipod_backup_devices", service.list_backup_devices())
        elif operation == "backup-snapshots":
            _emit(
                "ipod_backup_snapshots",
                service.list_backup_snapshots(
                    str(request.get("archive_id", "")),
                    int(request.get("page", 1)),
                    int(request.get("page_size", 50)),
                ),
            )
        elif operation == "backup-details":
            _emit(
                "ipod_backup_details",
                service.backup_snapshot_details(
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                ),
            )
        elif operation == "backup-verify":
            _emit(
                "ipod_backup_verify",
                service.verify_backup_snapshot(
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "backup-note":
            _emit(
                "ipod_backup_note",
                service.update_backup_note(
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    request.get("note", ""),
                ),
            )
        elif operation == "backup-manual":
            _emit(
                "ipod_backup_manual",
                service.manual_backup(
                    str(request.get("mount_path", "")),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "backup-export":
            _emit(
                "ipod_backup_export",
                service.export_backup_snapshot(
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    str(request.get("destination_dir", "")),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "backup-delete":
            _emit(
                "ipod_backup_delete",
                service.delete_backup_snapshot(
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    confirmed=bool(request.get("confirmed", False)),
                ),
            )
        elif operation == "restore-preflight":
            _emit(
                "ipod_restore_preflight",
                service.restore_preflight(
                    str(request.get("mount_path", "")),
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "restore":
            _emit(
                "ipod_restore",
                service.restore(
                    str(request.get("restore_plan_id", "")),
                    confirmed=bool(request.get("confirmed", False)),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "recovery-state":
            _emit("ipod_recovery_state", service.recovery_state())
        elif operation == "capacity-unlock-eligibility":
            _emit(
                "ipod_capacity_unlock_eligibility",
                service.capacity_unlock_eligibility(
                    str(request.get("mount_path", ""))
                ),
            )
        elif operation == "capacity-unlock-start":
            _emit(
                "ipod_capacity_unlock_start",
                service.start_capacity_unlock(
                    str(request.get("mount_path", "")),
                    request.get("confirmed") is True,
                    request.get("acknowledgements", {}),
                ),
            )
        elif operation == "capacity-unlock-advance":
            _emit(
                "ipod_capacity_unlock_advance",
                service.advance_capacity_unlock(
                    str(request.get("session_id", "")),
                    str(request.get("action", "")),
                    request.get("confirmed") is True,
                    request.get("data", {}),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "migration-preflight":
            _emit(
                "ipod_migration_preflight",
                service.migration_preflight(
                    str(request.get("mount_path", "")),
                    str(request.get("archive_id", "")),
                    str(request.get("snapshot_id", "")),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "migration":
            _emit(
                "ipod_migration",
                service.migration(
                    str(request.get("migration_plan_id", "")),
                    confirmed=bool(request.get("confirmed", False)),
                    progress=progress,
                    cancelled=cancelled,
                ),
            )
        elif operation == "plan":
            result = service.create_plan(
                str(request.get("mount_path", "")),
                request.get("source_files", []),
                progress=progress,
                cancelled=cancelled,
                staging_id=str(request.get("staging_id", "")),
            )
            _emit("ipod_plan", result)
        elif operation == "plan-details":
            result = service.plan_details(
                str(request.get("plan_id", "")),
                str(request.get("group", "")),
                int(request.get("page", 1)),
                int(request.get("page_size", 50)),
            )
            _emit("ipod_plan_details", result)
        elif operation == "backup":
            result = service.backup(
                str(request.get("plan_id", "")),
                progress=progress,
                cancelled=cancelled,
            )
            _emit("ipod_backup", result)
        elif operation == "execute":
            result = service.execute(
                str(request.get("plan_id", "")),
                confirmed=bool(request.get("confirmed", False)),
                progress=progress,
                cancelled=cancelled,
            )
            _emit("ipod_execute", result)
        elif operation == "cancel":
            decision = service.cancel(str(request.get("operation_id", "")))
            if decision.get("cancel_requested"):
                if not cancel_path:
                    raise IPodServiceError(
                        "invalid_request",
                        "A cancel_path is required for cancellation.",
                    )
                Path(cancel_path).parent.mkdir(parents=True, exist_ok=True)
                Path(cancel_path).write_text("cancel\n", encoding="utf-8")
            _emit("ipod_cancel", decision)
        elif operation == "eject":
            _emit("ipod_eject", service.eject(str(request.get("mount_path", ""))))
        elif operation == "stage":
            _emit(
                "ipod_stage",
                service.create_staging_contract(
                    str(request.get("mount_path", "")),
                    request.get("completed_files", []),
                    str(request.get("library_root", "")),
                ),
            )
        else:
            raise IPodServiceError("invalid_operation", f"Unsupported iPod operation: {operation or '(empty)'}")
        return 0
    except IPodServiceError as exc:
        extra = {"code": exc.code, "message": str(exc)}
        if exc.details:
            extra["details"] = exc.details
        _emit("ipod_error", **extra)
        return 2
    except ImportError:
        _emit(
            "ipod_error",
            code="iopenpod_unavailable",
            message="iOpenPod 1.67.1 is not installed in the Vela backend.",
        )
        return 2
    except PermissionError as exc:
        translated = _translate_write_safety_error(exc)
        _emit("ipod_error", code=translated.code, message=str(translated))
        return 2
    except Exception as exc:
        if exc.__class__.__name__ == "DeviceWriteSafetyError":
            translated = _translate_write_safety_error(exc)
            _emit("ipod_error", code=translated.code, message=str(translated))
            return 2
        _emit("ipod_error", code="unexpected_error", message=str(exc))
        return 2


def add_ipod_arguments(parser: Any) -> None:
    parser.add_argument(
        "--ipod-operation",
        choices=(
            "scan",
            "watch",
            "browse",
            "plan",
            "plan-details",
            "backup",
            "execute",
            "cancel",
            "eject",
            "stage",
            "backup-devices",
            "backup-snapshots",
            "backup-details",
            "backup-verify",
            "backup-note",
            "backup-manual",
            "backup-export",
            "backup-delete",
            "restore-preflight",
            "restore",
            "recovery-state",
            "capacity-unlock-eligibility",
            "capacity-unlock-start",
            "capacity-unlock-advance",
            "migration-preflight",
            "migration",
        ),
        help="Run one versioned headless iPod operation and exit",
    )
    parser.add_argument("--ipod-request", default="", help="Path to a versioned iPod request JSON object")
    parser.add_argument("--ipod-app-data", default="", help="Host app-data directory for reviewed plans/backups")
    parser.add_argument("--ipod-cancel-file", default="", help="Host-side cancellation sentinel path")
