"""
Abstract base class for all audio source adapters.
"""
from abc import ABC, abstractmethod
from enum import Enum
import threading
from typing import Optional

from antra.core.models import TrackMetadata, SearchResult, AudioFormat


class RateLimitedError(Exception):
    """
    Raised by a source adapter when it receives a 429 / rate-limit response.
    The engine treats this as a signal to skip this adapter immediately —
    no retry delay, no further attempts — and fall through to the next source.
    """

class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    NO_MATCH = "no_match"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    UNSUPPORTED = "unsupported"
    STORAGE = "storage"
    CANCELLED = "cancelled"
    DETERMINISTIC = "deterministic"


class ClassifiedSourceError(RuntimeError):
    def __init__(self, message: str, category: FailureCategory):
        super().__init__(message)
        self.category = category


class BaseSourceAdapter(ABC):
    """
    All source adapters implement this interface.
    search() returns the best available SearchResult or None.
    download() saves the audio file to disk and returns the path.
    """

    name: str = "base"
    priority: int = 99  # Lower = higher priority
    always_lossy: bool = False  # True for adapters that can never return lossless audio
    max_concurrent_searches: int = 8
    _progress_tls = threading.local()

    def set_download_progress_callback(self, callback) -> None:
        self._progress_tls.callback = callback

    def report_download_progress(
        self,
        bytes_downloaded: int,
        bytes_total: Optional[int] = None,
        phase: str = "transferring",
    ) -> None:
        callback = getattr(getattr(self, "_progress_tls", None), "callback", None)
        if callback:
            callback(max(0, int(bytes_downloaded)), bytes_total, phase)

    def write_stream_to_file(self, response, path: str, chunk_size: int = 131072) -> int:
        header = response.headers.get("Content-Length")
        total = int(header) if header and str(header).isdigit() else None
        downloaded = 0
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                self.report_download_progress(downloaded, total)
        return downloaded

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if credentials/dependencies are configured."""
        ...

    @abstractmethod
    def search(self, track: TrackMetadata) -> Optional[SearchResult]:
        """
        Search for the track. Return best SearchResult or None.
        Implementations should:
          1. Try ISRC first if available
          2. Fall back to title + artist search
          3. Score similarity using utils.matching
          4. Return None if best score < threshold
        """
        ...

    @abstractmethod
    def download(self, result: SearchResult, output_path: str) -> str:
        """
        Download audio to output_path (without extension).
        Return the full path with extension after successful download.
        Raise an exception on failure.
        """
        ...

    def hydrate_track_metadata(self, track: TrackMetadata, result: SearchResult) -> None:
        """Optionally enrich TrackMetadata using source-specific metadata."""
        return None

    def mark_failed_result(self, result: SearchResult, error: Exception) -> None:
        """Optionally blacklist a failed result so future searches can skip it."""
        return None

    def should_retry_download(self, result: SearchResult, error: Exception) -> bool:
        """Return False when retrying the same result would be wasted work."""
        return True

    def should_exclude_adapter_after_failure(
        self,
        result: SearchResult,
        error: Exception,
    ) -> bool:
        """Return False when the adapter can still provide alternative matches."""
        return True
