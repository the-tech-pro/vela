"""
Download engine — orchestrates resolve → download → tag → organize.
"""
import copy
import logging
import heapq
import hashlib
import os
import queue
import random
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, fields
from typing import Callable, Optional

from mutagen import File as MutagenFile

from antra.core.control import DownloadController
from antra.core.events import EngineEvent, EngineEventType
from antra.core.models import AudioFormat, TrackMetadata, DownloadResult, DownloadStatus
from antra.core.resolver import SourceResolver
from antra.sources.base import (
    ClassifiedSourceError,
    FailureCategory,
    RateLimitedError,
)
from antra.utils.matching import duration_close
from antra.utils.lyrics import LyricsFetcher
from antra.utils.organizer import LibraryOrganizer
from antra.utils.perf import elapsed_seconds, log_phase, start_phase
from antra.utils.tagger import FileTagger
from antra.utils.transcoder import AudioTranscoder

logger = logging.getLogger(__name__)

_LYRICS_STOP = object()


def _lyrics_snapshot(track: TrackMetadata) -> TrackMetadata:
    """Copy only normalized metadata fields, excluding dynamic runtime state."""
    return TrackMetadata(**{
        field.name: copy.deepcopy(getattr(track, field.name))
        for field in fields(TrackMetadata)
    })


@dataclass(frozen=True)
class _AudioProbeResult:
    """Audio properties read once for one immutable file fingerprint."""

    duration_seconds: float | None = None
    codec: str = ""
    bit_depth: int | None = None
    sample_rate: int | None = None
    bitrate: int | None = None
    channels: int | None = None
    has_info: bool = False


class _LyricsFetchTask:
    """Fetch lyrics from isolated metadata snapshots while audio work proceeds."""

    def __init__(self, fetcher: LyricsFetcher, track: TrackMetadata):
        self._fetcher = fetcher
        self._snapshots: queue.Queue[object] = queue.Queue()
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vela-lyrics")
        try:
            self._future = self._executor.submit(self._run, _lyrics_snapshot(track))
        except Exception:
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise

    def retry_with(self, track: TrackMetadata) -> None:
        """Queue the same retry the synchronous pipeline made after hydration."""
        try:
            snapshot = _lyrics_snapshot(track)
        except Exception as exc:
            logger.debug("  ℹ  Lyrics retry snapshot failed: %s", exc)
            return
        with self._lock:
            if self._closed or self._future.done():
                return
            self._snapshots.put(snapshot)

    def finish(self, track: TrackMetadata) -> None:
        """Wait for queued attempts and apply their result before final tagging."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._snapshots.put(_LYRICS_STOP)
        try:
            result = self._future.result()
        except Exception as exc:
            logger.debug("  ℹ  Lyrics fetch task failed: %s", exc)
            result = None
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)

        if result and not (track.lyrics or track.synced_lyrics):
            track.lyrics, track.synced_lyrics = result

    def cancel(self) -> None:
        """Stop queued retries without waiting for an in-flight bounded request."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._cancelled.set()
                self._snapshots.put(_LYRICS_STOP)
        self._future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, initial_snapshot: TrackMetadata) -> tuple[Optional[str], Optional[str]] | None:
        snapshot: object = initial_snapshot
        while snapshot is not _LYRICS_STOP and not self._cancelled.is_set():
            try:
                plain, synced = self._fetcher.fetch(snapshot)
            except Exception as exc:
                logger.debug("  ℹ  Lyrics fetch failed: %s", exc)
                plain, synced = None, None
            if self._cancelled.is_set():
                return None
            if plain or synced:
                return plain, synced
            snapshot = self._snapshots.get()
        return None


# errno values that indicate the output filesystem is no longer accessible
# (NAS disconnected, drive ejected, SMB session dropped after sleep, etc.)
_MOUNT_LOST_ERRNOS = frozenset({
    13,   # EACCES / EPERM  — permission denied (SMB session dropped)
    57,   # ENOTCONN        — socket not connected (macOS SMB after sleep)
    5,    # EIO             — I/O error (drive I/O failure)
    30,   # EROFS           — read-only filesystem (mount degraded)
    116,  # ESTALE          — stale NFS/SMB file handle
})


def _is_mount_lost_error(exc: BaseException) -> bool:
    """Return True if the exception looks like the output filesystem vanished."""
    return isinstance(exc, OSError) and exc.errno in _MOUNT_LOST_ERRNOS


def _is_server_error(exc: BaseException) -> bool:
    """Return True if the exception looks like a remote server 5xx failure.

    Used by the circuit breaker to distinguish between "track not found / auth
    issue" (which should not trip the breaker) and "the mirror server itself is
    down / returning 500" (which should rate-limit the adapter globally after
    3 consecutive failures so subsequent tracks skip it immediately).
    """
    msg = str(exc).lower()
    # Catch explicit HTTP status codes (500, 502, 503, 504, 507…)
    import re as _re
    if _re.search(r"\b5\d{2}\b", msg):
        return True
    # Catch phrased server errors from mirror adapters
    return any(kw in msg for kw in (
        "server error", "internal error",
        "service unavailable", "bad gateway",
    ))


def _summarize_source_error(msg: str) -> str:
    """Condense an adapter download-failure message into a short chain tag.

    Used for the per-track source-chain summary so users can see, at a glance,
    why each source could not deliver the track (rather than just the last error).
    """
    m = (msg or "").lower()
    if any(c in m for c in ("500", "502", "503", "504", "507")) or any(
        kw in m for kw in ("server error", "service unavailable", "bad gateway", "internal error")
    ):
        return "server error"
    if any(kw in m for kw in ("only available as high", "quality unconfirmable",
                              "quality mismatch", "lossless unavailable")):
        return "no lossless (AAC only)"
    if "truncated" in m or "preview" in m:
        return "truncated/preview"
    if "rate" in m and "limit" in m:
        return "rate-limited"
    if "no matching source" in m or "no source" in m or "no catalog match" in m:
        return "no catalog match"
    return (msg or "failed").strip()[:50]


def classify_download_failure(error: BaseException | str) -> FailureCategory:
    if isinstance(error, ClassifiedSourceError):
        return error.category
    if isinstance(error, RateLimitedError):
        return FailureCategory.RATE_LIMITED
    message = str(error or "").lower()
    if any(word in message for word in ("cancelled", "canceled")):
        return FailureCategory.CANCELLED
    if any(word in message for word in (
        "unauthorized", "authentication", "authorization", "token expired",
        "reconnect your account", "forbidden", "credential",
    )):
        return FailureCategory.AUTH
    if any(word in message for word in (
        "unsupported", "not supported", "drm-protected", "invalid format",
    )):
        return FailureCategory.UNSUPPORTED
    if any(word in message for word in (
        "output directory", "mount", "read-only filesystem", "disk full",
        "no space left", "permission denied",
    )):
        return FailureCategory.STORAGE
    if any(word in message for word in ("rate limit", "rate-limit", "429", "too many requests")):
        return FailureCategory.RATE_LIMITED
    if any(word in message for word in (
        "no matching source", "no source could find", "no catalog match",
        "no confident match", "track not found",
    )):
        return FailureCategory.NO_MATCH
    if any(word in message for word in (
        "timeout", "timed out", "connection reset", "connection aborted",
        "temporarily unavailable", "service unavailable", "bad gateway",
        "server error", "truncated", "500", "502", "503", "504",
    )):
        return FailureCategory.TRANSIENT
    return FailureCategory.DETERMINISTIC


@dataclass
class EngineConfig:
    max_retries: int = 3
    retry_delay: float = 5.0
    fetch_lyrics: bool = True
    fetch_artwork: bool = True
    save_cover_art_sidecar: bool = True
    output_format: str = "source"
    strict_matching: bool = False
    max_workers: int = 1
    auto_retry_window_seconds: float = 300.0
    auto_retry_backoff_seconds: float = 5.0


class DownloadEngine:
    def __init__(
        self,
        resolver: SourceResolver,
        organizer: LibraryOrganizer,
        lyrics_fetcher: Optional[LyricsFetcher] = None,
        config: Optional[EngineConfig] = None,
        event_callback: Optional[Callable[[EngineEvent], None]] = None,
        controller: Optional[DownloadController] = None,
    ):
        self.resolver = resolver
        self.organizer = organizer
        self.lyrics = lyrics_fetcher
        self.cfg = config or EngineConfig()
        self.tagger = FileTagger()
        self._probe_cache: dict[tuple[str, int | None, int | None], _AudioProbeResult] = {}
        self._probe_inflight: dict[tuple[str, int | None, int | None], threading.Event] = {}
        self._decode_probe_cache: dict[tuple[str, int | None, int | None], bool] = {}
        self._decode_probe_inflight: dict[tuple[str, int | None, int | None], threading.Event] = {}
        self._probe_lock = threading.Lock()
        self.transcoder = AudioTranscoder(probe_lookup=self._probe_audio)
        self.event_callback = event_callback
        self.controller = controller
        self._emit_lock = threading.Lock()
        # Set when a mount-loss error is detected mid-batch so remaining workers
        # can abort immediately instead of producing per-track error messages.
        self._output_lost = threading.Event()
        self._output_lost_message: str = ""
        # Per-adapter consecutive server-error counter (survives across tracks).
        # When an adapter hits 3 consecutive 5xx failures it is rate-limited for
        # 5 minutes so the resolver stops selecting it for subsequent tracks.
        self._adapter_server_errors: dict[str, int] = {}
        self._adapter_server_errors_lock = threading.Lock()
        self._event_context = threading.local()
        if self.controller:
            self.controller._on_state_change = self._emit_worker_state
            self._emit_worker_state(self.controller.worker_state())

    def _signal_output_lost(self, exc: OSError) -> None:
        """Record the first mount-loss error so workers can abort fast."""
        if not self._output_lost.is_set():
            self._output_lost_message = (
                f"Output directory became inaccessible mid-download "
                f"(errno {exc.errno}: {exc.strerror}). "
                "This usually means a NAS/network drive disconnected (e.g. Mac sleep). "
                "Remaining tracks skipped — re-queue to resume."
            )
            logger.error(f"  [MOUNT LOST]  {self._output_lost_message}")
            self._output_lost.set()

    def _emit(self, event_type: EngineEventType, **kwargs):
        if not self.event_callback:
            return
        kwargs.setdefault("job_id", getattr(self._event_context, "job_id", None))
        kwargs.setdefault("track_id", getattr(self._event_context, "track_id", None))
        with self._emit_lock:
            try:
                self.event_callback(EngineEvent(type=event_type, **kwargs))
            except Exception as e:
                logger.debug(f"Event callback failed: {e}")

    def _emit_worker_state(self, state: dict) -> None:
        self._emit(
            EngineEventType.WORKER_STATE,
            active_workers=state.get("active"),
            configured_workers=state.get("configured"),
            worker_ceiling=state.get("ceiling"),
            phase="paused" if state.get("paused") else "active",
        )

    @staticmethod
    def _track_event_id(track: TrackMetadata, index: int) -> str:
        recording_identity = (
            track.spotify_id
            or track.apple_music_id
            or track.deezer_track_id
            or track.tidal_track_id
            or track.isrc
            or f"{track.artist_string}|{track.title}|{track.album}|{track.duration_ms or 0}"
        )
        identity = f"{recording_identity}|occurrence:{index}"
        return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:20]

    def _emit_phase(self, phase: str, track: TrackMetadata, track_index: int, track_total: int) -> None:
        self._emit(
            EngineEventType.TRACK_PHASE,
            track=track,
            track_index=track_index,
            track_total=track_total,
            phase=phase,
        )

    @staticmethod
    def _hydrate_track_metadata(track: TrackMetadata, result) -> None:
        if (not track.album or track.album == "Unknown Album") and result.album:
            track.album = result.album
        if not track.artwork_url and getattr(result, "artwork_url", None):
            track.artwork_url = result.artwork_url

    def _fetch_lyrics_if_needed(self, track: TrackMetadata) -> None:
        if not self.cfg.fetch_lyrics or not self.lyrics:
            return
        if track.lyrics or track.synced_lyrics:
            return
        try:
            plain, synced = self.lyrics.fetch(track)
            track.lyrics = plain
            track.synced_lyrics = synced
        except Exception as e:
            logger.debug(f"  ℹ  Lyrics fetch failed: {e}")

    def _start_lyrics_fetch(self, track: TrackMetadata) -> _LyricsFetchTask | None:
        if not self.cfg.fetch_lyrics or not self.lyrics:
            return None
        if track.lyrics or track.synced_lyrics:
            return None
        try:
            return _LyricsFetchTask(self.lyrics, track)
        except Exception as exc:
            logger.debug("  ℹ  Lyrics background start failed: %s", exc)
            return None

    @staticmethod
    def _enrich_genres_if_needed(track: TrackMetadata) -> None:
        """Populate track.genres from MusicBrainz when Spotify didn't provide any."""
        if track.genres or not track.isrc:
            return
        try:
            from antra.utils.musicbrainz import fetch_genres
            genres = fetch_genres(track.isrc)
            if genres:
                track.genres = genres
                logger.debug(f"  [MB]  Genres for '{track.title}': {', '.join(genres)}")
        except Exception as e:
            logger.debug(f"  [MB]  Genre fetch failed: {e}")

    @staticmethod
    def _enrich_track_metadata_if_needed(track: TrackMetadata) -> None:
        """Fill missing metadata (ISRC, track number, release date, genre, artwork) from Deezer + iTunes."""
        from antra.utils.matching import score_similarity
        import re as _re

        needs_isrc = not track.isrc
        needs_track_num = not track.track_number
        needs_disc = not track.disc_number
        needs_date = not track.release_year and not track.release_date
        needs_genre = not track.genres
        needs_composer = not track.composer
        _is_spotify_art = bool(track.artwork_url and "i.scdn.co" in track.artwork_url)
        needs_art = not track.artwork_url or _is_spotify_art

        if not any([needs_isrc, needs_track_num, needs_disc, needs_date, needs_genre, needs_art, needs_composer]):
            return
        if not track.title or not track.artists:
            return

        try:
            import requests as _req
        except ImportError:
            return

        artist = track.artists[0]
        title = track.title

        # ── Deezer free API: ISRC, track position, disc, release date, artwork ──
        try:
            resp = _req.get(
                "https://api.deezer.com/search",
                params={"q": f'artist:"{artist}" track:"{title}"', "limit": 5},
                timeout=8,
            )
            if resp.status_code == 200:
                for hit in resp.json().get("data") or []:
                    hit_title = hit.get("title") or ""
                    hit_artist = (hit.get("artist") or {}).get("name") or ""
                    if score_similarity(title, track.artists, hit_title, hit_artist) < 0.60:
                        continue
                    if needs_isrc and hit.get("isrc"):
                        track.isrc = hit["isrc"]
                        needs_isrc = False
                        logger.debug("[MetaEnrich] ISRC from Deezer: %s", title)
                    if needs_track_num and hit.get("track_position"):
                        track.track_number = int(hit["track_position"])
                        needs_track_num = False
                        logger.debug("[MetaEnrich] Track# from Deezer: %s -> %s", title, track.track_number)
                    if needs_disc and hit.get("disk_number"):
                        track.disc_number = int(hit["disk_number"])
                        needs_disc = False
                    if needs_date:
                        rd = (hit.get("album") or {}).get("release_date") or ""
                        if rd:
                            track.release_date = rd
                            try:
                                track.release_year = int(rd[:4])
                            except (ValueError, TypeError):
                                pass
                            needs_date = False
                            logger.debug("[MetaEnrich] Date from Deezer: %s -> %s", title, rd)
                    if needs_art:
                        cover_xl = (hit.get("album") or {}).get("cover_xl") or ""
                        if cover_xl:
                            track.artwork_url = cover_xl
                            needs_art = False
                            logger.debug("[MetaEnrich] Art from Deezer: %s", title)
                    break
        except Exception as e:
            logger.debug("[MetaEnrich] Deezer failed for %r: %s", title, e)

        # ── iTunes Search API: track#, disc#, year, genre, composer, artwork ──
        if any([needs_track_num, needs_disc, needs_date, needs_genre, needs_art, needs_composer]):
            try:
                resp = _req.get(
                    "https://itunes.apple.com/search",
                    params={"term": f"{artist} {title}", "entity": "song", "limit": 8, "country": "us"},
                    timeout=8,
                )
                if resp.status_code == 200:
                    for hit in resp.json().get("results") or []:
                        if hit.get("wrapperType") != "track":
                            continue
                        hit_title = hit.get("trackName") or ""
                        hit_artist = hit.get("artistName") or ""
                        if score_similarity(title, track.artists, hit_title, hit_artist) < 0.60:
                            continue
                        if needs_track_num and hit.get("trackNumber"):
                            track.track_number = int(hit["trackNumber"])
                            needs_track_num = False
                            logger.debug("[MetaEnrich] Track# from iTunes: %s -> %s", title, track.track_number)
                        if needs_disc and hit.get("discNumber"):
                            track.disc_number = int(hit["discNumber"])
                            needs_disc = False
                        if needs_date and not track.release_year:
                            rd = hit.get("releaseDate") or ""
                            if rd and len(rd) >= 4 and rd[:4].isdigit():
                                track.release_year = int(rd[:4])
                                track.release_date = rd[:10]
                                needs_date = False
                                logger.debug("[MetaEnrich] Year from iTunes: %s -> %s", title, track.release_year)
                        if needs_genre and hit.get("primaryGenreName"):
                            track.genres = [hit["primaryGenreName"]]
                            needs_genre = False
                            logger.debug("[MetaEnrich] Genre from iTunes: %s -> %s", title, track.genres)
                        if needs_composer and hit.get("composerName"):
                            track.composer = hit["composerName"]
                            needs_composer = False
                        if needs_art and hit.get("artworkUrl100"):
                            track.artwork_url = _re.sub(r"\d+x\d+bb", "3000x3000bb", hit["artworkUrl100"])
                            needs_art = False
                            logger.debug("[MetaEnrich] Art from iTunes: %s", title)
                        break
            except Exception as e:
                logger.debug("[MetaEnrich] iTunes failed for %r: %s", title, e)

    @staticmethod
    def _metadata_debug_snapshot(track: TrackMetadata) -> dict:
        """Compact metadata snapshot for post-resolve / pre-tag diagnostics."""
        return {
            "title": track.title,
            "album": track.album,
            "artists": track.artists or [],
            "album_artists": track.album_artists or [],
            "isrc": track.isrc or "",
            "genres": track.genres or [],
            "composer": track.composer or "",
            "release_year": track.release_year,
            "release_date": track.release_date or "",
            "track_number": track.track_number,
            "disc_number": track.disc_number,
            "artwork_url": bool(track.artwork_url),
        }

    @classmethod
    def _log_pre_tag_metadata_diagnostics(
        cls,
        track: TrackMetadata,
        result,
        file_path: str,
        adapter_name: str,
        before_snapshot: dict,
    ) -> None:
        """Log a structured before/after enrichment snapshot and flag missing key tags."""
        source_meta = getattr(result, "source_metadata", None) or {}
        after_snapshot = cls._metadata_debug_snapshot(track)
        logger.debug(
            "  [META] pre-tag adapter=%s file=%s source_meta=%s before=%s after=%s",
            adapter_name,
            file_path,
            source_meta,
            before_snapshot,
            after_snapshot,
        )
        if source_meta.get("isrc") and not track.isrc:
            logger.warning(
                "  [META] Resolver returned ISRC %r for '%s' via %s, but track.isrc is still empty before tagging.",
                source_meta.get("isrc"),
                track.title,
                adapter_name,
            )
        if source_meta.get("isrc") and not track.genres:
            diagnostics = getattr(track, "_antra_meta_diag", None) or {}
            logger.warning(
                "  [META] Genre still missing before tagging '%s' via %s despite resolver ISRC %r. "
                "This means post-resolve enrichment did not recover genre. diagnostics=%s",
                track.title,
                adapter_name,
                source_meta.get("isrc"),
                diagnostics,
            )

    @staticmethod
    def _file_fingerprint(file_path: str) -> tuple[str, int | None, int | None]:
        normalized_path = os.path.normcase(os.path.abspath(file_path))
        try:
            stat = os.stat(file_path)
        except OSError:
            return normalized_path, None, None
        return normalized_path, stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _probe_int(value) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _probe_audio(self, file_path: str) -> _AudioProbeResult:
        """Return one singleflight Mutagen/ffprobe result per file fingerprint."""
        fingerprint = self._file_fingerprint(file_path)
        while True:
            with self._probe_lock:
                if fingerprint in self._probe_cache:
                    return self._probe_cache[fingerprint]
                flight = self._probe_inflight.get(fingerprint)
                if flight is None:
                    flight = threading.Event()
                    self._probe_inflight[fingerprint] = flight
                    owner = True
                else:
                    owner = False
            if owner:
                break
            flight.wait()

        probe = _AudioProbeResult()
        try:
            probe = self._read_audio_probe(file_path)
        except Exception as exc:
            logger.debug("[Engine] Audio probe failed for %s: %s", file_path, exc)
        finally:
            with self._probe_lock:
                self._probe_cache[fingerprint] = probe
                self._probe_inflight.pop(fingerprint, None)
                flight.set()
        return probe

    def _read_audio_probe(self, file_path: str) -> _AudioProbeResult:
        audio = None
        mutagen_failed = False
        try:
            audio = MutagenFile(file_path)
        except Exception:
            mutagen_failed = True
        info = getattr(audio, "info", None)

        codec = str(getattr(info, "codec", "") or "").lower() if info else ""
        bit_depth = self._probe_int(getattr(info, "bits_per_sample", None)) if info else None
        sample_rate = self._probe_int(getattr(info, "sample_rate", None)) if info else None
        bitrate = self._probe_int(getattr(info, "bitrate", None)) if info else None
        channels = self._probe_int(getattr(info, "channels", None)) if info else None

        ext = os.path.splitext(file_path)[1].lower()
        duration: float | None = None
        if ext == ".m4a":
            duration = self._probe_duration_seconds_with_ffprobe(file_path)
        elif not mutagen_failed:
            length = getattr(info, "length", None) if info else None
            try:
                duration = float(length) if length is not None else None
            except (TypeError, ValueError):
                duration = None
            if duration is None:
                duration = self._probe_duration_seconds_with_ffprobe(file_path)

        return _AudioProbeResult(
            duration_seconds=duration,
            codec=codec,
            bit_depth=bit_depth,
            sample_rate=sample_rate,
            bitrate=bitrate,
            channels=channels,
            has_info=bool(info),
        )

    def _audio_format_from_path(self, file_path: str) -> AudioFormat | None:
        ext = file_path.lower().rsplit(".", 1)[-1] if "." in file_path else ""
        basic = {
            "flac": AudioFormat.FLAC,
            "mp3": AudioFormat.MP3,
            "aac": AudioFormat.AAC,
        }.get(ext)
        if basic is not None:
            return basic
        if ext == "m4a":
            if self._probe_audio(file_path).codec.startswith("alac"):
                return AudioFormat.ALAC
            return AudioFormat.AAC
        return None

    def _quality_label_from_file(
        self,
        file_path: str,
        fallback_format: AudioFormat | None,
        fallback_label: str,
    ) -> str:
        probe = self._probe_audio(file_path)
        detected_format = self._audio_format_from_path(file_path) or fallback_format
        if probe.has_info and detected_format is not None:
            fmt = detected_format.value.upper()
            if detected_format in {AudioFormat.FLAC, AudioFormat.ALAC}:
                if probe.bit_depth and probe.sample_rate:
                    return f"{fmt} {probe.bit_depth}-bit/{probe.sample_rate // 1000}kHz"
                if probe.bit_depth:
                    return f"{fmt} {probe.bit_depth}-bit"
                return fmt
            if probe.bitrate:
                return f"{fmt} {probe.bitrate // 1000}kbps"
            return fmt
        return fallback_label

    def _should_convert_output(self, file_path: str, output_format: str) -> bool:
        return self.transcoder.needs_conversion(file_path, output_format)

    @staticmethod
    def _format_conversion_log(file_path: str, output_format: str) -> str:
        ext = os.path.splitext(file_path)[1].lower() or "source"
        base_format = output_format.split("-")[0] if output_format.endswith(("-16", "-24")) else output_format
        if base_format in {"lossless", "flac"}:
            return f"Preparing FLAC output from {ext}"
        if base_format == "alac":
            return f"Preparing ALAC output from {ext}"
        if base_format in {"aac", "m4a", "mp3"}:
            return f"Transcoding to {base_format.upper()} from {ext}"
        return f"Converting to {output_format} from {ext}"

    def _requires_lossless_output(self) -> bool:
        return self.cfg.output_format in {"flac", "lossless", "alac", "lossless-16", "lossless-24", "alac-16", "alac-24"}

    def _is_lossy_output_mode(self) -> bool:
        return self.cfg.output_format in {"mp3", "aac", "m4a"}

    def _probe_duration_seconds(self, file_path: str) -> float | None:
        return self._probe_audio(file_path).duration_seconds

    @staticmethod
    def _probe_duration_seconds_with_ffprobe(file_path: str) -> float | None:
        from antra.utils.runtime import get_ffprobe_exe
        ffprobe = get_ffprobe_exe() or shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except (TypeError, ValueError):
            return None

    def _is_truncated_download(self, file_path: str, expected_duration_ms: int | None) -> bool:
        return self._get_truncation_reason(file_path, expected_duration_ms) is not None

    def _get_truncation_reason(
        self,
        file_path: str,
        expected_duration_ms: int | None,
        result_duration_ms: int | None = None,
        strict_matching: bool = False,
    ) -> str | None:
        if not expected_duration_ms or expected_duration_ms < 60000:
            return None

        tiny_lossless_reason = self._get_tiny_lossless_file_reason(file_path, expected_duration_ms)
        if tiny_lossless_reason is not None:
            return tiny_lossless_reason

        actual_seconds = self._probe_duration_seconds(file_path)
        if actual_seconds is None:
            return self._get_flac_truncation_reason(file_path)
        expected_seconds = expected_duration_ms / 1000.0

        def _severe_mismatch(expected_s: float, actual_s: float) -> bool:
            shorter = (
                actual_s < expected_s * 0.8
                and (expected_s - actual_s) >= 20
            )
            longer = (
                actual_s > expected_s * 1.3
                and (actual_s - expected_s) >= 45
            )
            return shorter or longer

        # If the source result reported its own duration and the file matches it,
        # the download is complete — but only when the source duration is itself
        # reasonably close to the expected metadata duration. Otherwise a mirror
        # can serve the wrong recording (or a preview) while staying internally
        # self-consistent, which would let bad audio slip through.
        if result_duration_ms and result_duration_ms >= 60000:
            result_seconds = result_duration_ms / 1000.0
            source_matches_expected = not _severe_mismatch(expected_seconds, result_seconds)
            if source_matches_expected and abs(actual_seconds - result_seconds) <= result_seconds * 0.05 + 5:
                # File matches the source's own declared duration — not truncated.
                return self._get_flac_truncation_reason(file_path)

        # Gross duration mismatch check for all formats. This catches both
        # preview clips (~30s) and completely different full tracks.
        if _severe_mismatch(expected_seconds, actual_seconds):
            return (
                f"duration mismatch: got {actual_seconds:.1f}s "
                f"but expected {expected_seconds:.1f}s"
            )

        if strict_matching and not duration_close(expected_seconds, actual_seconds, tolerance=8):
            return (
                f"strict duration mismatch: got {actual_seconds:.1f}s "
                f"but expected {expected_seconds:.1f}s"
            )

        # Secondary file-size check for FLAC files.
        return self._get_flac_truncation_reason(file_path)

    @staticmethod
    def _get_tiny_lossless_file_reason(file_path: str, expected_duration_ms: int | None) -> str | None:
        if not expected_duration_ms or expected_duration_ms < 60000:
            return None
        if not file_path.lower().endswith((".flac", ".m4a")):
            return None
        try:
            actual_size = os.path.getsize(file_path)
        except OSError:
            return "downloaded file is missing"

        expected_seconds = expected_duration_ms / 1000.0
        # A full-length lossless file in the low hundreds of KB is always a bad
        # delivery: preview HTML, an aborted stream, or a header-only container.
        absolute_floor = 512 * 1024
        per_second_floor = expected_seconds * 2 * 1024
        min_expected_size = int(max(absolute_floor, min(per_second_floor, 2 * 1024 * 1024)))
        if actual_size < min_expected_size:
            return (
                f"implausibly small lossless file: got {actual_size / 1024:.0f}KB "
                f"for expected {expected_seconds:.1f}s track"
            )
        return None

    def _is_truncated_flac_by_size(self, file_path: str) -> bool:
        return self._get_flac_truncation_reason(file_path) is not None

    def _get_flac_truncation_reason(self, file_path: str) -> str | None:
        """
        Detect truncated FLAC downloads by comparing actual file size against
        the minimum expected size based on the FLAC header's own metadata.

        FLAC headers write the total sample count up front, so Mutagen
        reports the full *intended* duration even when the file was truncated
        mid-stream.  This check catches those cases.

        Only runs on .flac files. Small hi-res acoustic masters can compress
        much harder than a fixed size floor suggests, so we treat a low
        size-to-PCM ratio as suspicious and confirm it with a real decode
        probe before declaring the file truncated.
        """
        if not file_path.lower().endswith(".flac"):
            return None

        try:
            probe = self._probe_audio(file_path)
            bits = probe.bit_depth
            rate = probe.sample_rate
            channels = probe.channels
            length = probe.duration_seconds

            if not all((bits, rate, channels, length)):
                return None
            if length < 60:
                return None  # Don't flag short tracks

            actual_size = os.path.getsize(file_path)
            # Raw PCM size for the declared duration
            raw_pcm_bytes = length * rate * channels * (bits / 8)
            # FLAC typically compresses to 50-70% of raw.
            # Use 0.25 as a suspicion threshold only. Some valid sparse masters
            # can dip below this, especially 24-bit/96kHz acoustic material.
            min_expected_bytes = raw_pcm_bytes * 0.25

            if actual_size < min_expected_bytes:
                ratio = actual_size / raw_pcm_bytes if raw_pcm_bytes > 0 else 0
                logger.debug(
                    f"[Engine] FLAC size check: {file_path} is {actual_size / (1024*1024):.1f}MB "
                    f"vs suspicious floor {min_expected_bytes / (1024*1024):.1f}MB "
                    f"(ratio={ratio:.2f}, {bits}bit/{rate}Hz/{length:.0f}s) — running decode probe"
                )
                if self._fails_flac_decode_probe(file_path):
                    return (
                        f"suspicious FLAC failed decode probe "
                        f"(ratio={ratio:.2f}, {bits}bit/{rate}Hz/{length:.0f}s)"
                    )

        except Exception as e:
            logger.debug(f"[Engine] FLAC size check failed: {e}")

        return None

    def _fails_flac_decode_probe(self, file_path: str) -> bool:
        """Return one singleflight ffmpeg decode result per file fingerprint."""
        fingerprint = self._file_fingerprint(file_path)
        while True:
            with self._probe_lock:
                if fingerprint in self._decode_probe_cache:
                    return self._decode_probe_cache[fingerprint]
                flight = self._decode_probe_inflight.get(fingerprint)
                if flight is None:
                    flight = threading.Event()
                    self._decode_probe_inflight[fingerprint] = flight
                    owner = True
                else:
                    owner = False
            if owner:
                break
            flight.wait()

        failed = False
        try:
            failed = self._run_flac_decode_probe(file_path)
        except Exception as exc:
            logger.debug("[Engine] FLAC decode probe failed unexpectedly: %s", exc)
        finally:
            with self._probe_lock:
                self._decode_probe_cache[fingerprint] = failed
                self._decode_probe_inflight.pop(fingerprint, None)
                flight.set()
        return failed

    @staticmethod
    def _run_flac_decode_probe(file_path: str) -> bool:
        """Return True when ffmpeg cannot fully decode the FLAC cleanly."""
        try:
            from antra.utils.runtime import get_ffmpeg_exe

            ffmpeg = get_ffmpeg_exe() or shutil.which("ffmpeg")
            if not ffmpeg:
                logger.debug("[Engine] FLAC decode probe skipped — ffmpeg unavailable")
                return False

            result = subprocess.run(
                [ffmpeg, "-v", "error", "-i", file_path, "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except Exception as e:
            logger.debug(f"[Engine] FLAC decode probe failed to run: {e}")
            return False

        stderr = (result.stderr or "").strip()
        if result.returncode != 0 or stderr:
            logger.debug(
                f"[Engine] FLAC decode probe failed for {file_path}: "
                f"exit={result.returncode} stderr={stderr[-300:]}"
            )
            return True

        return False

    @staticmethod
    def _discard_file(path: str) -> None:
        import os

        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def download_track(
        self,
        track: TrackMetadata,
        track_index: Optional[int] = None,
        track_total: Optional[int] = None,
        defer_failure: bool = False,
    ) -> DownloadResult:
        """Run one track and always retire its background lyrics worker."""
        lyrics_tasks: list[_LyricsFetchTask] = []
        try:
            return self._download_track_impl(
                track,
                track_index=track_index,
                track_total=track_total,
                defer_failure=defer_failure,
                _lyrics_tasks=lyrics_tasks,
            )
        finally:
            for task in lyrics_tasks:
                task.cancel()

    def _download_track_impl(
        self,
        track: TrackMetadata,
        track_index: Optional[int],
        track_total: Optional[int],
        defer_failure: bool,
        _lyrics_tasks: list[_LyricsFetchTask],
    ) -> DownloadResult:
        """Full pipeline for a single track."""
        self._emit_phase("resolving", track, track_index or 0, track_total or 0)

        # 1. Resume check — only skip if the existing file meets the current output format.
        exact_output_existed = self.organizer.has_exact_output(track)
        existing = self.organizer.is_already_downloaded(track)
        if existing:
            # In lossless-only mode, don't accept a previously-downloaded lossy file.
            # Re-download it as lossless instead.
            if self._requires_lossless_output():
                ext = os.path.splitext(existing)[1].lower()
                lossy_extensions = {".mp3", ".aac", ".m4a"}
                # .m4a could be ALAC (lossless) — check the actual codec
                if ext in lossy_extensions:
                    is_lossy_file = True
                    if ext == ".m4a":
                        # alac codec = lossless; mp4a = AAC = lossy
                        is_lossy_file = "alac" not in self._probe_audio(existing).codec
                    if is_lossy_file:
                        logger.info(
                            f"  [REDOWNLOAD]  '{track.title}' exists as lossy {ext} "
                            f"but lossless mode is active — re-downloading as lossless."
                        )
                        existing = None  # fall through to download

            if existing:
                existing = self.organizer.ensure_request_copy(track, existing)
                if self.cfg.save_cover_art_sidecar:
                    self.tagger.save_cover_art_sidecar(existing, track)
                if not exact_output_existed:
                    logger.info(f"  [LOCAL] Materialized from local audio: {track.title}")
                    self._emit(
                        EngineEventType.TRACK_COMPLETED,
                        track=track,
                        track_index=track_index,
                        track_total=track_total,
                        file_path=existing,
                        message="Downloaded from local audio cache.",
                    )
                    return DownloadResult(
                        track=track,
                        status=DownloadStatus.COMPLETED,
                        file_path=existing,
                    )
                logger.info(f"  [SKIP]  Skipping (already downloaded): {track.title}")
                self._emit(
                    EngineEventType.TRACK_SKIPPED,
                    track=track,
                    track_index=track_index,
                    track_total=track_total,
                    file_path=existing,
                    message="Track already exists on disk.",
                )
                return DownloadResult(
                    track=track,
                    status=DownloadStatus.SKIPPED,
                    file_path=existing,
                )

        # 2. Fetch from isolated snapshots while resolve and transfer continue.
        lyrics_task = self._start_lyrics_fetch(track)
        if lyrics_task is not None:
            _lyrics_tasks.append(lyrics_task)

        excluded_adapters: set[str] = set()
        # Adapters that were rate-limited get a second chance after all other
        # sources are exhausted (rate limit may have cleared by then).
        rate_limited_adapters: set[str] = set()
        # Once an adapter has been given its second chance, permanently exclude it.
        rate_limited_retried: set[str] = set()
        last_error: Optional[str] = None
        last_source: Optional[str] = None
        attempted_sources: list[str] = []  # ordered list of sources that were tried
        used_lossy_fallback: bool = False  # flag for post-download warning
        # Per-adapter outcome across the whole track (search + download), so the
        # full source chain can be surfaced if the track ultimately fails.
        source_chain: dict[str, str] = {}

        while True:
            # 3. Resolve — skip both permanently-excluded and currently rate-limited adapters.
            all_excluded = excluded_adapters | rate_limited_adapters
            resolve_started = start_phase()
            resolution = self.resolver.resolve(track, excluded_adapters=all_excluded)
            log_phase(logger, "resolve", resolve_started, subject=track.title)
            # Merge this cycle's search outcomes (no-match / search-error / found)
            # into the running chain. Excluded adapters aren't re-searched, so a
            # prior download-failure reason for them is preserved; the selected
            # adapter's "found" entry is overridden below if its download fails.
            source_chain.update(self.resolver.last_resolve_report())
            if not resolution:
                # Before giving up: if any adapters were rate-limited and haven't
                # had their one retry yet, unblock them and try again.
                newly_retryable = rate_limited_adapters - rate_limited_retried
                if newly_retryable:
                    logger.info(
                        f"  [RATE]  All other sources exhausted — retrying rate-limited: "
                        f"{', '.join(newly_retryable)}"
                    )
                    rate_limited_retried |= newly_retryable
                    rate_limited_adapters.clear()
                    continue

                if last_error:
                    user_error = last_error
                elif attempted_sources:
                    user_error = (
                        f"No matching source found — tried: {', '.join(attempted_sources)}"
                    )
                else:
                    fmt = self.cfg.output_format or "auto"
                    user_error = (
                        f"No source could find this track in {fmt.upper()} mode. "
                        "The track may not be in the catalog of any configured lossless service."
                    )
                if (
                    getattr(track, "amazon_asin", None)
                    and self._is_lossy_output_mode()
                    and "amazon" in excluded_adapters
                ):
                    user_error = (
                        "Amazon could not provide a playable file for this track, "
                        "and no safe YouTube fallback match was found."
                    )
                # Surface the full per-source outcome so it's clear every source was
                # tried (and why each one couldn't deliver) — not just the last error.
                if source_chain:
                    chain_str = ", ".join(
                        f"{name}: {reason}" for name, reason in source_chain.items()
                    )
                    logger.info(f"  [CHAIN]  {track.title} — sources tried → {chain_str}")
                if not defer_failure:
                    self.organizer.mark_failed(track, user_error)
                    self._emit(
                        EngineEventType.TRACK_FAILED,
                        track=track,
                        track_index=track_index,
                        track_total=track_total,
                        source=last_source,
                        error=user_error,
                    )
                return DownloadResult(
                    track=track,
                    status=DownloadStatus.FAILED,
                    source_used=last_source,
                    error_message=user_error,
                    attempt_count=self.cfg.max_retries,
                )

            result, adapter = resolution
            # Track if we ended up using a lossy source in lossless-prefer mode
            # (so we can emit a post-download warning). The resolver already handles
            # the "prefer lossless, fall back to lossy as last resort" logic.
            if self._requires_lossless_output() and not result.is_lossless:
                used_lossy_fallback = True
            self._hydrate_track_metadata(track, result)
            adapter.hydrate_track_metadata(track, result)
            if lyrics_task is not None:
                lyrics_task.retry_with(track)
            # Layout must use post-hydration metadata (album/year from the resolver, etc.)
            try:
                output_base = self.organizer.get_output_path(track)
            except OSError as e:
                if _is_mount_lost_error(e):
                    self._signal_output_lost(e)
                raise
            self._emit(
                EngineEventType.TRACK_RESOLVED,
                track=track,
                track_index=track_index,
                track_total=track_total,
                source=adapter.name,
                quality_label=result.quality_label,
                message=f"Resolved via {adapter.name}",
            )

            file_path: Optional[str] = None
            final_error: Optional[Exception] = None

            for attempt in range(1, self.cfg.max_retries + 1):
                attempt_started = start_phase()
                try:
                    source_text = adapter.name
                            
                    self._emit(
                        EngineEventType.TRACK_DOWNLOAD_ATTEMPT,
                        track=track,
                        track_index=track_index,
                        track_total=track_total,
                        source=source_text,
                        quality_label=result.quality_label,
                        attempt=attempt,
                    )
                    source_quality = result.quality_label
                    if getattr(result, "sample_rate", None):
                        source_quality += f" / {result.sample_rate / 1000}kHz"

                    if attempt == 1:
                        logger.info(
                            f"  \U0001f4e5 [Downloading] [{track_index}/{track_total}] {track.title} by {track.artist_string} ({source_quality})"
                        )
                    else:
                        logger.info(
                            f"  \U0001f501 [Retry {attempt}] [{track_index}/{track_total}] {track.title} ({source_quality})"
                        )
                    progress_started = time.monotonic()
                    last_progress_emit = 0.0

                    def report_progress(downloaded: int, total_bytes: Optional[int], phase: str = "transferring"):
                        nonlocal last_progress_emit
                        now = time.monotonic()
                        if now - last_progress_emit < 0.2 and (not total_bytes or downloaded < total_bytes):
                            return
                        last_progress_emit = now
                        elapsed = max(0.001, now - progress_started)
                        percent = (
                            min(100.0, max(0.0, downloaded * 100.0 / total_bytes))
                            if total_bytes and total_bytes > 0 else None
                        )
                        self._emit(
                            EngineEventType.TRACK_PROGRESS,
                            track=track,
                            track_index=track_index,
                            track_total=track_total,
                            phase=phase,
                            bytes_downloaded=downloaded,
                            bytes_total=total_bytes,
                            progress_percent=percent,
                            speed_bps=downloaded / elapsed,
                            source=adapter.name,
                        )

                    adapter.set_download_progress_callback(report_progress)
                    self._emit_phase("transferring", track, track_index or 0, track_total or 0)
                    try:
                        candidate_path = adapter.download(result, output_base)
                    finally:
                        adapter.set_download_progress_callback(None)
                    # Probe actual duration before transcoding — used as the
                    # authoritative reference for the truncation check below.
                    # Amazon OPUS streams may be a different edit than the
                    # Spotify metadata suggests; probing before conversion
                    # gives us the true source duration.
                    _pre_transcode_duration_s = self._probe_duration_seconds(candidate_path)
                    source_duration_ms: int | None = (
                        int(_pre_transcode_duration_s * 1000)
                        if _pre_transcode_duration_s is not None else None
                    )
                    if self._should_convert_output(candidate_path, self.cfg.output_format):
                        logger.info(
                            "  [FMT]  %s: %s",
                            self._format_conversion_log(candidate_path, self.cfg.output_format),
                            track.title,
                        )
                        try:
                            candidate_path = self.transcoder.convert(candidate_path, self.cfg.output_format)
                        except RuntimeError as conv_err:
                            # ffmpeg failed — discard the corrupt source so it does not
                            # linger on disk and re-raise so the engine falls through to
                            # the next adapter (Apple DRM-locked M4A being the primary case).
                            self._discard_file(candidate_path)
                            raise RuntimeError(
                                f"[{adapter.name}] Audio conversion failed — "
                                f"source file may be corrupt or DRM-protected: {conv_err}"
                            ) from conv_err
                        except (KeyError, ValueError) as conv_err:
                            # Unsupported format string (e.g. 'lossless-24' in old binary) —
                            # keep the file as-is rather than crashing the whole engine.
                            logger.warning(
                                f"  [FMT]  Format conversion skipped ({conv_err}) — "
                                f"keeping source file: {candidate_path}"
                            )
                    truncation_reason = self._get_truncation_reason(
                        candidate_path,
                        track.duration_ms,
                        result_duration_ms=source_duration_ms,
                        strict_matching=self.cfg.strict_matching,
                    )
                    if truncation_reason is not None:
                        self._discard_file(candidate_path)
                        raise RuntimeError(
                            f"[{adapter.name}] Download appears truncated for {track.title} "
                            f"({truncation_reason})"
                        )
                    # Quality verification: if the adapter claimed 24-bit hi-res but
                    # the actual file is 16-bit, discard and try the next source.
                    # Amazon in particular hardcodes bit_depth=24 in search results
                    # but sometimes serves 16-bit streams (track-level quality < album).
                    claimed_bit_depth = getattr(result, "bit_depth", None)
                    if (
                        claimed_bit_depth is not None
                        and claimed_bit_depth >= 24
                        and (self.cfg.output_format or "").lower() in {"lossless", "lossless-24", "flac", "alac", "source", ""}
                        and candidate_path.lower().endswith((".flac", ".m4a"))
                    ):
                        _actual_bd = self._probe_audio(candidate_path).bit_depth
                        if _actual_bd and _actual_bd < 24:
                            self._discard_file(candidate_path)
                            raise RuntimeError(
                                f"[{adapter.name}] Quality mismatch for '{track.title}': "
                                f"claimed {claimed_bit_depth}-bit but delivered {_actual_bd}-bit — "
                                f"retrying with next source"
                            )
                    file_path = candidate_path
                    break
                except Exception as e:
                    if _is_mount_lost_error(e):
                        self._signal_output_lost(e)
                    final_error = e
                    last_error = str(e)
                    last_source = adapter.name
                    if adapter.name not in attempted_sources:
                        attempted_sources.append(adapter.name)
                    # A download failure is more informative than the search outcome —
                    # override the chain entry for this adapter.
                    source_chain[adapter.name] = _summarize_source_error(str(e))
                    self.resolver.record_album_source_failure(track, adapter.name)
                    adapter.mark_failed_result(result, e)

                    # Rate-limited: skip to next source immediately — no sleep, no retry.
                    if isinstance(e, RateLimitedError):
                        logger.info(f"  [RATE]  {adapter.name} rate-limited — falling back to next source immediately")
                        if adapter.name in rate_limited_retried:
                            # Already gave this adapter its one retry — permanently exclude.
                            excluded_adapters.add(adapter.name)
                        else:
                            # Defer for a possible second chance after other sources are tried.
                            rate_limited_adapters.add(adapter.name)
                        break

                    will_retry = attempt < self.cfg.max_retries and adapter.should_retry_download(result, e)
                    if adapter.name == "hifi" and "all quality levels failed" in str(e).lower():
                        logger.info("  [INFO]  HiFi mirrors could not provide a valid stream. Trying next source...")
                    elif will_retry:
                        if "appears truncated" in str(e):
                            logger.info(f"  [TRUNC]  Attempt {attempt} truncated — retrying... ({e})")
                        else:
                            logger.debug(f"  [RETRY] Attempt {attempt} failed, retrying... ({e})")
                    else:
                        # Final failure for this adapter — surface it
                        logger.warning(f"  [WARN]  Attempt {attempt} failed: {e}")
                    if will_retry:
                        time.sleep(self.cfg.retry_delay)
                        continue
                    break

            if file_path:
                self._emit_phase("processing", track, track_index or 0, track_total or 0)
                # 4. Enrich metadata from winning adapter + free APIs + lyrics + art
                pre_enrich_snapshot = self._metadata_debug_snapshot(track)
                try:
                    from antra.core.metadata_enricher import MetadataEnricher
                    MetadataEnricher.enrich(track, result)
                except Exception:
                    self._enrich_track_metadata_if_needed(track)
                    self._enrich_genres_if_needed(track)
                if lyrics_task is not None:
                    # Let metadata enrichment overlap the in-flight lyrics request.
                    # If it supplied a stronger identity, queue one final snapshot
                    # before joining so tagging still receives the best result.
                    lyrics_task.retry_with(track)
                    lyrics_started = start_phase()
                    lyrics_task.finish(track)
                    log_phase(logger, "lyrics wait", lyrics_started, subject=track.title)
                self._log_pre_tag_metadata_diagnostics(
                    track,
                    result,
                    file_path,
                    adapter.name,
                    pre_enrich_snapshot,
                )
                logger.debug(
                    "  [TAG]  %s | album=%r artwork=%s lyrics=%s synced=%s genres=%s",
                    file_path,
                    track.album,
                    bool(track.artwork_url),
                    bool(track.lyrics),
                    bool(track.synced_lyrics),
                    track.genres or [],
                )
                tag_ok = self.tagger.tag(file_path, track)
                if not tag_ok:
                    logger.warning(
                        f"  [WARN]  Metadata tagging did not complete for {file_path}. "
                        "This usually means the output container is unsupported for embedded tags."
                    )
                if self.cfg.save_cover_art_sidecar:
                    self.tagger.save_cover_art_sidecar(file_path, track)

                # 5. Mark done
                self.organizer.mark_downloaded(track, file_path)
                # Persist a successful delivery so this adapter is preferred within
                # its tier on future downloads / sessions (SF-1).
                self.resolver.record_outcome(adapter.name, True)
                actual_bit_depth = self._probe_audio(file_path).bit_depth
                self.resolver.record_album_source_success(
                    track,
                    adapter.name,
                    result,
                    actual_bit_depth=actual_bit_depth,
                )

                elapsed = elapsed_seconds(attempt_started)
                if elapsed is not None:
                    size_mb = os.path.getsize(file_path) / (1024 * 1024) if os.path.exists(file_path) else 0
                    logger.info(
                        "  [TIMING]  %s transfer+process %.2fs (%.1f MB, %.1f MB/s)",
                        track.title,
                        elapsed,
                        size_mb,
                        size_mb / max(elapsed, 0.001),
                    )
                
                logger.info(
                    f"  \u2728 [Complete] [{track_index}/{track_total}] {track.title} by {track.artist_string}"
                )
                if used_lossy_fallback:
                    logger.warning(
                        f"  \u26a0\ufe0f  [{track.title}] No lossless source available — "
                        f"downloaded as {result.quality_label} from {adapter.name}. "
                        f"Not true lossless."
                    )
                completed_audio_format = self._audio_format_from_path(file_path) or result.audio_format
                completed_quality_label = self._quality_label_from_file(
                    file_path,
                    completed_audio_format,
                    result.quality_label,
                )
                self._emit(
                    EngineEventType.TRACK_COMPLETED,
                    track=track,
                    track_index=track_index,
                    track_total=track_total,
                    source=adapter.name,
                    file_path=file_path,
                    quality_label=completed_quality_label,
                )
                return DownloadResult(
                    track=track,
                    status=DownloadStatus.COMPLETED,
                    file_path=file_path,
                    source_used=adapter.name,
                    audio_format=completed_audio_format,
                )

            # Rate-limited adapters already placed in rate_limited_adapters above — skip
            # the regular exclude logic so they don't also land in excluded_adapters.
            if isinstance(final_error, RateLimitedError):
                # Reliability signal: the adapter had the track but is overloaded (SF-1).
                self.resolver.record_outcome(adapter.name, False)
                continue

            # Truncated downloads: the adapter found the track but the stream ended early
            # (network blip, proxy cut it off). Don't permanently exclude — instead:
            # 1. Mark the adapter as globally rate-limited in the resolver (120s cooldown)
            #    so ALL parallel workers immediately start preferring other adapters.
            #    Without this, workers running in parallel each independently queue on the
            #    broken adapter, discovering the truncation one at a time.
            # 2. Defer the adapter to the end of this track's queue (rate_limited_adapters)
            #    so Amazon/HiFi get a fair shot first; the adapter gets one last retry if
            #    nothing else works (useful when the adapter is the only one that can find
            #    the track, e.g. featured-artist titles that defeat Amazon/HiFi search).
            if final_error is not None and "appears truncated" in str(final_error):
                # Signal all parallel workers to stop queuing on this adapter.
                self.resolver._mark_rate_limited(adapter.name, cooldown_seconds=120)
                # Reliability signal: a truncated/preview stream is a delivery failure (SF-1).
                self.resolver.record_outcome(adapter.name, False)

                if adapter.name in rate_limited_retried:
                    # Already had its second chance and still truncated — give up.
                    excluded_adapters.add(adapter.name)
                    logger.info(f"  [NEXT]  {adapter.name} truncated on second attempt — no more retries")
                else:
                    logger.info(
                        f"  [TRUNC]  {adapter.name} truncated — trying other sources first, "
                        f"will retry {adapter.name} as last resort if nothing else works"
                    )
                    rate_limited_adapters.add(adapter.name)
                continue

            should_exclude = True
            if final_error is not None:
                should_exclude = adapter.should_exclude_adapter_after_failure(result, final_error)

            if should_exclude:
                excluded_adapters.add(adapter.name)
                if (
                    adapter.name == "amazon"
                    and getattr(track, "amazon_asin", None)
                    and self._is_lossy_output_mode()
                ):
                    logger.info("  [NEXT]  Amazon could not provide a usable file — trying YouTube fallback...")
                else:
                    logger.info(f"  [NEXT]  {adapter.name} failed after retries, trying next source...")
                # Circuit breaker: if the failure looks like a server-side 5xx
                # (not a missing track or auth issue), count consecutive failures.
                # After 3 in a row, rate-limit the adapter globally for 5 minutes
                # so it is skipped for all subsequent tracks in this session.
                if final_error is not None and _is_server_error(final_error):
                    # Reliability signal: server-side 5xx delivery failure (SF-1).
                    self.resolver.record_outcome(adapter.name, False)
                    with self._adapter_server_errors_lock:
                        count = self._adapter_server_errors.get(adapter.name, 0) + 1
                        self._adapter_server_errors[adapter.name] = count
                    if count >= 3:
                        logger.warning(
                            f"  [CIRCUIT]  {adapter.name} has failed with server errors "
                            f"{count} times — marking unavailable for 5 minutes."
                        )
                        self.resolver._mark_rate_limited(adapter.name, cooldown_seconds=300)
                        with self._adapter_server_errors_lock:
                            self._adapter_server_errors[adapter.name] = 0
                else:
                    # Non-server-error failure (404, auth, no match) resets the counter.
                    with self._adapter_server_errors_lock:
                        self._adapter_server_errors.pop(adapter.name, None)
            else:
                logger.info(f"  [NEXT]  {adapter.name} candidate failed, trying another match from the same source...")

    def download_playlist(self, tracks: list[TrackMetadata]) -> list[DownloadResult]:
        """Download all tracks in a playlist in parallel, returning results in original order."""
        total = len(tracks)
        playlist_name = tracks[0].playlist_name if tracks and tracks[0].playlist_name else None
        self._emit(
            EngineEventType.PLAYLIST_STARTED,
            track_total=total,
            message=f"Starting playlist download for {total} track(s).",
        )

        # results[i] will hold the DownloadResult for tracks[i]
        results: list[Optional[DownloadResult]] = [None] * total
        job_seed = f"{playlist_name or 'download'}|{time.time_ns()}|{total}"
        job_id = hashlib.sha256(job_seed.encode("utf-8")).hexdigest()[:16]
        retry_deadlines: dict[int, float] = {}
        retry_attempts: dict[int, int] = {}
        retry_results: dict[int, DownloadResult] = {}

        def _worker(index: int, track: TrackMetadata, retry_attempt: int = 0) -> tuple[int, DownloadResult]:
            self._event_context.job_id = job_id
            self._event_context.track_id = self._track_event_id(track, index)
            # Abort immediately if the output filesystem was lost by a previous worker.
            if self._output_lost.is_set():
                result = DownloadResult(
                    track=track,
                    status=DownloadStatus.FAILED,
                    error_message=self._output_lost_message,
                )
                self._event_context.job_id = None
                self._event_context.track_id = None
                return index, result
            slot_acquired = False
            if self.controller:
                slot_acquired = self.controller.acquire_worker_slot()
                if not slot_acquired or self.controller.is_cancelled():
                    result = DownloadResult(
                        track=track,
                        status=DownloadStatus.CANCELLED,
                        error_message="Cancelled",
                    )
                    self._event_context.job_id = None
                    self._event_context.track_id = None
                    return index, result
            try:
                logger.info(f"[{index + 1}/{total}] {track.artist_string} — {track.title}")
                if retry_attempt == 0:
                    self._emit(
                        EngineEventType.TRACK_STARTED,
                        track=track,
                        track_index=index + 1,
                        track_total=total,
                    )
                return index, self.download_track(
                    track,
                    track_index=index + 1,
                    track_total=total,
                    defer_failure=True,
                )
            finally:
                if self.controller and slot_acquired:
                    self.controller.release_worker_slot()
                self._event_context.job_id = None
                self._event_context.track_id = None

        # Park enough threads for this device; the controller gates live starts.
        workers = self.controller.worker_ceiling if self.controller else max(1, self.cfg.max_workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            initial_queue = deque(range(total))
            delayed: list[tuple[float, int, int]] = []

            def finalize_failure(idx: int, result: DownloadResult, attempt: int, exhausted: bool) -> None:
                self._event_context.job_id = job_id
                self._event_context.track_id = self._track_event_id(tracks[idx], idx)
                if exhausted:
                    self._emit(
                        EngineEventType.TRACK_RETRY_EXHAUSTED,
                        track=tracks[idx],
                        track_index=idx + 1,
                        track_total=total,
                        attempt=attempt,
                        phase="failed",
                        error=result.error_message,
                    )
                self.organizer.mark_failed(tracks[idx], result.error_message or "Download failed")
                self._emit(
                    EngineEventType.TRACK_FAILED,
                    track=tracks[idx],
                    track_index=idx + 1,
                    track_total=total,
                    attempt=attempt,
                    phase="failed",
                    error=result.error_message,
                )
                results[idx] = result
                retry_results.pop(idx, None)
                self._event_context.job_id = None
                self._event_context.track_id = None

            while pending or delayed or initial_queue:
                if self.controller and self.controller.is_cancelled():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                now = time.monotonic()
                while len(pending) < workers:
                    if delayed and delayed[0][0] <= now:
                        _, idx, attempt = heapq.heappop(delayed)
                        if now >= retry_deadlines.get(idx, 0):
                            result = retry_results.get(idx)
                            if result is not None:
                                finalize_failure(idx, result, attempt, True)
                            continue
                        pending[pool.submit(_worker, idx, tracks[idx], attempt)] = idx
                        continue
                    if initial_queue:
                        idx = initial_queue.popleft()
                        pending[pool.submit(_worker, idx, tracks[idx], 0)] = idx
                        continue
                    break

                timeout = 0.2
                if delayed:
                    timeout = max(0.01, min(timeout, delayed[0][0] - time.monotonic()))
                if not pending:
                    time.sleep(timeout)
                    continue

                done, _ = wait(set(pending), timeout=timeout, return_when=FIRST_COMPLETED)
                for future in done:
                    idx = pending.pop(future)
                    try:
                        _, result = future.result()
                    except OSError as exc:
                        if _is_mount_lost_error(exc):
                            self._signal_output_lost(exc)
                        result = DownloadResult(
                            track=tracks[idx],
                            status=DownloadStatus.FAILED,
                            error_message=self._output_lost_message if self._output_lost.is_set() else str(exc),
                        )
                    except Exception as exc:
                        logger.warning(f"Worker for track {idx + 1} raised unexpectedly: {exc}")
                        result = DownloadResult(
                            track=tracks[idx],
                            status=DownloadStatus.FAILED,
                            error_message=str(exc),
                        )

                    if result.status != DownloadStatus.FAILED:
                        results[idx] = result
                        continue

                    category = classify_download_failure(result.error_message or "Download failed")
                    retryable = category in {
                        FailureCategory.TRANSIENT,
                        FailureCategory.NO_MATCH,
                        FailureCategory.RATE_LIMITED,
                    }
                    attempt = retry_attempts.get(idx, 0) + 1
                    retry_attempts[idx] = attempt
                    deadline = retry_deadlines.setdefault(
                        idx,
                        time.monotonic() + max(0.0, self.cfg.auto_retry_window_seconds),
                    )
                    base_retry_delay = max(0.01, self.cfg.auto_retry_backoff_seconds)
                    base_delay = (
                        max(15.0, base_retry_delay)
                        if category == FailureCategory.RATE_LIMITED
                        else min(60.0, base_retry_delay * (2 ** (attempt - 1)))
                    )
                    delay = base_delay * random.uniform(0.85, 1.15)

                    self._event_context.job_id = job_id
                    self._event_context.track_id = self._track_event_id(tracks[idx], idx)
                    if retryable and time.monotonic() + delay < deadline:
                        retry_results[idx] = result
                        heapq.heappush(delayed, (time.monotonic() + delay, idx, attempt))
                        self._emit(
                            EngineEventType.TRACK_RETRY_SCHEDULED,
                            track=tracks[idx],
                            track_index=idx + 1,
                            track_total=total,
                            attempt=attempt,
                            phase="retry_wait",
                            error=result.error_message,
                            retry_after_seconds=delay,
                            retry_deadline=time.time() + max(0.0, deadline - time.monotonic()),
                        )
                    else:
                        finalize_failure(idx, result, attempt, retryable)
                    self._event_context.job_id = None
                    self._event_context.track_id = None

        # Fill any slots that were cancelled or never completed
        final: list[DownloadResult] = []
        for i, r in enumerate(results):
            if r is None:
                r = DownloadResult(
                    track=tracks[i],
                    status=DownloadStatus.CANCELLED,
                    error_message="Cancelled",
                )
            final.append(r)

        if self.controller and self.controller.is_cancelled():
            if playlist_name and final:
                self.organizer.write_playlist_manifest(
                    playlist_name,
                    [r.file_path for r in final if r.file_path],
                )
            self._emit(
                EngineEventType.PLAYLIST_CANCELLED,
                track_total=total,
                message="Playlist download cancelled.",
            )
            return final

        if playlist_name:
            self.organizer.write_playlist_manifest(
                playlist_name,
                [r.file_path for r in final if r.file_path],
            )

        self._emit(
            EngineEventType.PLAYLIST_COMPLETED,
            track_total=total,
            message=f"Processed {len(final)} track(s).",
        )

        # If mount loss was detected, raise so json_cli surfaces the error in
        # the playlist_summary and subsequent URLs are also skipped cleanly.
        if self._output_lost.is_set():
            raise OSError(self._output_lost_message)

        return final
