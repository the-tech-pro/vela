"""
Structured events emitted by the download engine.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from antra.core.models import TrackMetadata


class EngineEventType(Enum):
    PLAYLIST_STARTED = "playlist_started"
    PLAYLIST_COMPLETED = "playlist_completed"
    PLAYLIST_CANCELLED = "playlist_cancelled"
    TRACK_STARTED = "track_started"
    TRACK_SKIPPED = "track_skipped"
    TRACK_RESOLVED = "track_resolved"
    TRACK_DOWNLOAD_ATTEMPT = "track_download_attempt"
    TRACK_PROGRESS = "track_progress"
    TRACK_PHASE = "track_phase"
    TRACK_RETRY_SCHEDULED = "track_retry_scheduled"
    TRACK_RETRY_EXHAUSTED = "track_retry_exhausted"
    WORKER_STATE = "worker_state"
    TRACK_COMPLETED = "track_completed"
    TRACK_FAILED = "track_failed"


@dataclass
class EngineEvent:
    type: EngineEventType
    track: Optional[TrackMetadata] = None
    track_index: Optional[int] = None
    track_total: Optional[int] = None
    message: Optional[str] = None
    source: Optional[str] = None
    quality_label: Optional[str] = None
    attempt: Optional[int] = None
    file_path: Optional[str] = None
    error: Optional[str] = None
    job_id: Optional[str] = None
    track_id: Optional[str] = None
    phase: Optional[str] = None
    bytes_downloaded: Optional[int] = None
    bytes_total: Optional[int] = None
    progress_percent: Optional[float] = None
    speed_bps: Optional[float] = None
    active_workers: Optional[int] = None
    configured_workers: Optional[int] = None
    worker_ceiling: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    retry_deadline: Optional[float] = None
