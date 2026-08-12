from __future__ import annotations

import json

import pytest

from antra.core.ipod_operation_journal import (
    OperationJournal,
    OperationJournalError,
)


def test_journal_recovers_incomplete_commit_atomically(tmp_path):
    now = [100.0]
    directory = tmp_path / "journal"
    journal = OperationJournal(directory, clock=lambda: now[0])
    started = journal.start(
        "restore",
        phase="verifying",
        target_id="target-device",
        source_archive_id="SERIAL123",
        snapshot_id="20260812T120000_000001Z",
    )
    now[0] += 1
    journal.transition(
        started["operation_id"],
        "committing",
        can_cancel=False,
        safety_snapshot_id="20260812T120100_000001Z",
        reconnect={"required_device_id": "target-device"},
        recovery={"required": True, "next_action": "reconnect"},
    )

    recovered = OperationJournal(directory, clock=lambda: now[0]).recovery_state()
    assert recovered["incomplete"] is True
    assert recovered["requires_recovery"] is True
    assert recovered["operation"]["phase"] == "committing"
    assert recovered["operation"]["can_cancel"] is False
    assert (
        recovered["operation"]["safety_snapshot_id"]
        == "20260812T120100_000001Z"
    )
    assert not list(directory.glob("*.tmp"))
    on_disk = json.loads(
        (directory / f"{started['operation_id']}.json").read_text(encoding="utf-8")
    )
    assert on_disk["revision"] == 2


def test_journal_terminal_state_is_immutable(tmp_path):
    journal = OperationJournal(tmp_path / "journal")
    operation = journal.start("backup_verify", phase="verifying")
    journal.finish(operation["operation_id"], "succeeded")

    with pytest.raises(OperationJournalError, match="terminal"):
        journal.transition(
            operation["operation_id"],
            "committing",
            can_cancel=False,
        )
    state = journal.recovery_state()
    assert state["incomplete"] is False
    assert state["operation"]["status"] == "succeeded"
    assert state["operation"]["can_cancel"] is False
