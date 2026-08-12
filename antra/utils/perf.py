"""Opt-in, local-only performance measurement helpers.

Set ``VELA_PERF=1`` for timing and payload-size lines in the existing local
logs. The helpers never transmit or persist measurements themselves.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Mapping, Optional


PERF_ENV_VAR = "VELA_PERF"
_TRUTHY = frozenset({"1", "true", "yes", "on", "debug"})
PERF_ENABLED = os.environ.get(PERF_ENV_VAR, "").strip().lower() in _TRUTHY


def perf_enabled() -> bool:
    """Return the process-wide opt-in state captured during import."""
    return PERF_ENABLED


def start_phase() -> Optional[int]:
    """Return a monotonic start timestamp, or ``None`` when disabled."""
    if not PERF_ENABLED:
        return None
    return time.perf_counter_ns()


def elapsed_seconds(started_ns: Optional[int]) -> Optional[float]:
    """Convert a start timestamp to elapsed seconds without wall-clock drift."""
    if started_ns is None:
        return None
    return max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000_000)


def log_phase(
    logger: logging.Logger,
    phase: str,
    started_ns: Optional[int],
    *,
    subject: str = "",
    counts: Optional[Mapping[str, int]] = None,
) -> Optional[float]:
    """Write one existing-style ``[TIMING]`` line for an enabled phase."""
    elapsed = elapsed_seconds(started_ns)
    if elapsed is None:
        return None
    label = f"{subject} {phase}".strip()
    suffix = ""
    if counts:
        suffix = "".join(
            f" {name}={int(value)}"
            for name, value in sorted(counts.items())
        )
    logger.info("  [TIMING]  %s %.2fs%s", label, elapsed, suffix)
    return elapsed


def payload_size_bytes(payload: Any) -> int:
    """Return the UTF-8 wire size of text, bytes, or a JSON-compatible value."""
    if isinstance(payload, bytes):
        return len(payload)
    if isinstance(payload, bytearray):
        return len(payload)
    if isinstance(payload, memoryview):
        return payload.nbytes
    if isinstance(payload, str):
        return len(payload.encode("utf-8"))
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return len(encoded.encode("utf-8"))


def log_payload(
    logger: logging.Logger,
    name: str,
    payload: Any,
) -> Optional[int]:
    """Log a payload's byte size only when local performance mode is enabled."""
    if not PERF_ENABLED:
        return None
    size = payload_size_bytes(payload)
    logger.info("  [TIMING]  %s payload %d bytes", name, size)
    return size
