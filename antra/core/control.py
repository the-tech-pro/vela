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

    def __init__(self, control_path: str | None = None, initial_workers: int = 2):
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._cancel_event = threading.Event()
        self._control_path = control_path or os.environ.get("ANTRA_CONTROL_PATH", "")
        self._desired_workers = max(1, min(8, int(initial_workers or 2)))
        self._active_workers = 0
        self._condition = threading.Condition()
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
                    return True
                self._condition.wait(timeout=0.2)
        return False

    def release_worker_slot(self) -> None:
        with self._condition:
            self._active_workers = max(0, self._active_workers - 1)
            self._condition.notify_all()

    def _refresh_external_state(self) -> None:
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
            self._desired_workers = max(1, min(8, int(state.get("max_workers", self._desired_workers))))
            if bool(state.get("paused", False)):
                self._resume_event.clear()
            else:
                self._resume_event.set()
            self._condition.notify_all()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()
