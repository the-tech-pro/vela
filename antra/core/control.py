"""
Cooperative download control helpers for future desktop frontends.

This currently supports pausing/resuming between tracks and cancelling
before the next track starts. It does not interrupt an in-flight download.
"""
import json
import os
import threading
import time


class DownloadController:
    """Thread-safe pause/resume/cancel state for long-running downloads."""

    def __init__(
        self,
        control_path: str | None = None,
        initial_workers: int = 2,
        on_state_change=None,
    ):
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._cancel_event = threading.Event()
        self._control_path = control_path or os.environ.get("ANTRA_CONTROL_PATH", "")
        logical_cpus = os.cpu_count() or 4
        adaptive_ceiling = 8 if logical_cpus <= 4 else 12 if logical_cpus <= 8 else 16
        self._worker_ceiling = max(
            8,
            min(16, int(os.environ.get("ANTRA_WORKER_CEILING", adaptive_ceiling))),
        )
        self._desired_workers = max(1, min(self._worker_ceiling, int(initial_workers or 2)))
        self._active_workers = 0
        self._condition = threading.Condition()
        self._on_state_change = on_state_change
        self._last_control_check = 0.0
        self._last_control_mtime = -1.0

    def pause(self):
        self._resume_event.clear()

    def resume(self):
        self._resume_event.set()

    def cancel(self):
        self._cancel_event.set()
        self._resume_event.set()

    def wait_if_paused(self):
        while not self.is_cancelled():
            self._refresh_external_state()
            if self._resume_event.wait(timeout=0.2):
                return

    def acquire_worker_slot(self) -> bool:
        """Gate new work using the live desktop worker limit."""
        with self._condition:
            while not self.is_cancelled():
                self._refresh_external_state()
                if self._resume_event.is_set() and self._active_workers < self._desired_workers:
                    self._active_workers += 1
                    self._notify_state_change()
                    return True
                self._condition.wait(timeout=0.2)
        return False

    def release_worker_slot(self) -> None:
        with self._condition:
            self._active_workers = max(0, self._active_workers - 1)
            self._condition.notify_all()
            self._notify_state_change()

    def _refresh_external_state(self) -> None:
        with self._condition:
            if not self._control_path or time.monotonic() - self._last_control_check < 0.15:
                return
            self._last_control_check = time.monotonic()
            try:
                mtime = os.path.getmtime(self._control_path)
                if mtime == self._last_control_mtime:
                    return
                with open(self._control_path, "r", encoding="utf-8") as handle:
                    state = json.load(handle)
                self._last_control_mtime = mtime
                self._desired_workers = max(
                    1,
                    min(self._worker_ceiling, int(state.get("max_workers", self._desired_workers))),
                )
                if bool(state.get("paused", False)):
                    self._resume_event.clear()
                else:
                    self._resume_event.set()
                self._condition.notify_all()
                self._notify_state_change()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return

    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def worker_ceiling(self) -> int:
        return self._worker_ceiling

    def worker_state(self) -> dict:
        return {
            "active": self._active_workers,
            "configured": self._desired_workers,
            "ceiling": self._worker_ceiling,
            "paused": not self._resume_event.is_set(),
        }

    def _notify_state_change(self) -> None:
        if not self._on_state_change:
            return
        try:
            self._on_state_change(self.worker_state())
        except Exception:
            pass
