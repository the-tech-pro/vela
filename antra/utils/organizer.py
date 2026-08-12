"""
Handles library layout, global deduplication, and playlist manifest generation.
"""
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TSRC, TXXX
from mutagen.mp4 import MP4

from antra.core.models import TrackMetadata
from antra_shared.filename_prefs import (
    build_folder_path,
    build_single_track_stem,
    build_track_stem,
    migrate_legacy_templates,
)

logger = logging.getLogger(__name__)

STATE_FILE = ".antra_state.json"
IDENTITY_INDEX_FILE = ".antra_identity_index.json"
IDENTITY_INDEX_SCHEMA_VERSION = 1
TRACK_KEY_PREFIX = "TRACK:"
FAILED_PREFIX = "FAILED:"
SUPPORTED_AUDIO_EXTENSIONS = (".flac", ".mp3", ".aac", ".m4a", ".mp4", ".opus")
_PERSIST_BATCH_MUTATIONS = 4
_PERSIST_DELAY_SECONDS = 0.25
_FILE_LOCK_TIMEOUT_SECONDS = 0.2
_FILE_LOCK_STALE_SECONDS = 30.0


@contextmanager
def _exclusive_file_lock(
    path: Path,
    *,
    timeout: float = _FILE_LOCK_TIMEOUT_SECONDS,
) -> Iterator[bool]:
    """Best-effort cross-process lock using an atomic lock-file create.

    Persistence is deliberately lock tolerant: callers keep their dirty
    in-memory updates and retry later when another process owns the lock.
    Stale lock files left by a terminated process are reclaimed.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    token = f"{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex}"
    acquired = False
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(descriptor, token.encode("ascii"))
            except OSError:
                try:
                    path.unlink()
                except OSError:
                    pass
                break
            finally:
                os.close(descriptor)
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > _FILE_LOCK_STALE_SECONDS:
                    path.unlink()
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
                continue
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        except OSError:
            break

    try:
        yield acquired
    finally:
        if acquired:
            try:
                if path.read_text(encoding="ascii") == token:
                    path.unlink()
            except (FileNotFoundError, OSError, UnicodeError):
                pass


def _read_json_object(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_replace_json(path: Path, value: dict[str, Any]) -> bool:
    """Write JSON through a unique same-directory file and atomic replace."""
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as exc:
        logger.warning("Could not persist %s: %s", path.name, exc)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


class LibraryOrganizer:
    """
    Library structure (no Albums/ or Playlists/ wrappers):
      <root>/<Artist>/<Album (Year)>/<NN - Track Title>.<ext>   (standard mode)
      <root>/<Album (Year)>/<NN - Track Title>.<ext>            (flat mode)
      <root>/<Playlist Name>/<NN - Track Title>.<ext>
      <root>/<Playlist Name>.m3u

    Deduplication is global across the library. The first downloaded file path
    becomes canonical and later playlist/album/song downloads reuse that file.
    """

    def __init__(
        self,
        root: str,
        full_albums: bool = False,
        folder_structure: str = "standard",
        album_folder_structure: str = "",
        playlist_folder_structure: str = "",
        single_track_structure: str = "album_numbered",
        filename_format: str = "default",
        single_track_filename_template: str = "",
        album_track_filename_template: str = "",
        folder_structure_template: str = "",
        multi_disc_handling: str = "prefix",
        track_number_padding: int = 2,
        illegal_character_replacement: str = "",
        whitespace_handling: str = "preserve",
        filename_conflict_behavior: str = "skip",
    ):
        self.root = Path(root).resolve()
        self.full_albums = full_albums
        legacy_structure = folder_structure or "standard"
        self.folder_structure = legacy_structure
        self.album_folder_structure = album_folder_structure or legacy_structure
        self.playlist_folder_structure = playlist_folder_structure or legacy_structure
        self.single_track_structure = single_track_structure or "album_numbered"
        self.filename_format = filename_format
        self.filename_preferences = migrate_legacy_templates(
            {
                "single_track_filename_template": single_track_filename_template,
                "album_track_filename_template": album_track_filename_template,
                "folder_structure_template": folder_structure_template,
                "multi_disc_handling": multi_disc_handling,
                "track_number_padding": track_number_padding,
                "illegal_character_replacement": illegal_character_replacement,
                "whitespace_handling": whitespace_handling,
                "filename_conflict_behavior": filename_conflict_behavior,
            },
            filename_format=filename_format,
            album_folder_structure=self.album_folder_structure,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        # No Albums/ or Playlists/ wrappers — everything lives directly under root.
        self.albums_root = self.root
        self.playlists_root = self.root
        self._state_path = self.root / STATE_FILE
        self._identity_cache_path = self.root / IDENTITY_INDEX_FILE
        self._state_lock = threading.RLock()
        self._pending_state_updates: dict[str, str] = {}
        self._pending_mutations = 0
        self._persistence_timer: Optional[threading.Timer] = None
        self._identity_records: dict[str, dict[str, Any]] = {}
        self._identity_pending_upserts: dict[str, dict[str, Any]] = {}
        self._identity_pending_deletions: dict[str, dict[str, Any]] = {}
        self._identity_force_rewrite = False
        self._identity_cache_complete = True
        self._state = self._load_state()
        self._identity_index: dict[str, str] = {}
        self._identity_candidates: dict[str, list[str]] = {}
        if not full_albums:
            self._build_identity_index()
        else:
            self._load_identity_records_without_scan()

    # ── Public API ────────────────────────────────────────────────────────

    def get_output_path(self, track: TrackMetadata) -> str:
        """Return the target output path WITHOUT extension."""
        if track.playlist_name:
            playlist_dir = self._safe(track.playlist_name)
            track_number = track.playlist_position or track.track_number
            filename = self._format_filename(track, track_number, disc_number=1)
            if self.playlist_folder_structure == "flat":
                folder = self.root / playlist_dir
            else:
                folder = self.playlists_root / playlist_dir
            folder.mkdir(parents=True, exist_ok=True)
            return str(self._resolve_conflict_path(folder, filename, track=track))

        if (track.request_kind or "").lower() == "track":
            return self._single_track_output_path(track)

        folder = self._album_folder(track)
        filename = self._format_filename(track, track.track_number)
        folder.mkdir(parents=True, exist_ok=True)
        return str(self._resolve_conflict_path(folder, filename, track=track))

    def _single_track_output_path(self, track: TrackMetadata) -> str:
        if self.single_track_structure == "file":
            folder = self.root
            filename = build_single_track_stem(track, self.filename_preferences)
        else:
            folder = self._album_folder(track)
            filename = build_single_track_stem(track, self.filename_preferences)
        folder.mkdir(parents=True, exist_ok=True)
        return str(self._resolve_conflict_path(folder, filename, track=track))

    @staticmethod
    def _extract_album_from_folder_leaf(leaf: str) -> str:
        """Strip year decorations to get just the album name from a rendered folder leaf.

        Handles all year formats produced by folder_structure_template:
          "2011 - Zonoscope"   → "Zonoscope"
          "Zonoscope (2011)"   → "Zonoscope"
          "(2011) Zonoscope"   → "Zonoscope"
        """
        s = re.sub(r"^\d{4}\s*[-–—]\s*", "", leaf)
        s = re.sub(r"^\(\d{4}\)\s*", "", s)
        s = re.sub(r"\s*\(\d{4}\)$", "", s)
        s = re.sub(r"\s*[-–—]\s*\d{4}$", "", s)
        return s.strip()

    def _album_folder(self, track: TrackMetadata) -> Path:
        custom_path = build_folder_path(track, self.filename_preferences)
        if custom_path:
            parts = custom_path.split("/")
            target = self.root.joinpath(*parts)
            # Year-variant dedup: when the exact target doesn't exist yet, look for
            # a sibling folder whose name is the same album with a different year
            # (e.g. "2010 - Zonoscope" already exists when we try to create
            # "2011 - Zonoscope").  Different metadata sources often return slightly
            # different release years for the same album, creating split folders.
            if not target.exists() and len(parts) >= 1:
                parent = target.parent
                album_name = self._extract_album_from_folder_leaf(parts[-1])
                if album_name:
                    existing = self._find_existing_album_folder(parent, album_name)
                    if existing is not None:
                        return existing
            return target
        # Use album-level artists for the folder name so joint albums
        # (e.g. "PARTYNEXTDOOR & Drake") land in one combined folder
        # instead of splitting by per-track artist.
        if track.album_artists:
            artist_dir = self._safe(", ".join(track.album_artists))
        else:
            artist_dir = self._safe(", ".join(track.artists))
        album_part = self._safe(track.album)
        if track.release_year:
            if self.album_folder_structure == "year_prefix":
                album_dir = f"({track.release_year}) {album_part}"
            else:
                album_dir = f"{album_part} ({track.release_year})"
        else:
            album_dir = album_part

        if self.album_folder_structure == "flat":
            parent = self.root
        else:
            parent = self.albums_root / artist_dir

        # If a year-variant of this album folder already exists on disk, reuse
        # it instead of creating a new folder with a different year.  This
        # prevents duplicate folders like "Anthology 1 (1963)" / "Anthology 1
        # (1995)" / "Anthology 1 (1996)" when the same album is downloaded from
        # different sources that report different release dates.
        target = parent / album_dir
        if not target.exists() and track.release_year:
            existing = self._find_existing_album_folder(parent, album_part)
            if existing is not None:
                return existing

        return target

    def _find_existing_album_folder(self, parent: Path, album_part: str) -> Optional[Path]:
        """Return an existing year-variant folder for *album_part* under *parent*.

        Looks for directories whose name matches ``<album_part> (<year>)`` or
        exactly ``<album_part>`` (no year).  Returns the first match found, or
        ``None`` if the parent directory doesn't exist yet.
        """
        if not parent.exists():
            return None
        import re as _re
        escaped = _re.escape(album_part)
        # Match both "Album (Year)" / "Album" and "(Year) Album" naming styles
        # so that switching between standard and year_prefix modes doesn't
        # create duplicate folders for the same album.
        pat_suffix = _re.compile(rf"^{escaped}(?:\s*\(\d{{4}}\))?$", _re.IGNORECASE)
        pat_prefix = _re.compile(rf"^\(\d{{4}}\)\s*{escaped}$", _re.IGNORECASE)
        # Also match "Year - Album" format used by the default folder_structure_template
        pat_year_dash = _re.compile(rf"^\d{{4}}\s*[-–—]\s*{escaped}$", _re.IGNORECASE)
        for child in parent.iterdir():
            if child.is_dir() and (
                pat_suffix.match(child.name)
                or pat_prefix.match(child.name)
                or pat_year_dash.match(child.name)
            ):
                return child
        return None

    def _format_filename(
        self,
        track: TrackMetadata,
        track_number: Optional[int],
        *,
        disc_number: Optional[int] = None,
    ) -> str:
        return build_track_stem(track, self.filename_preferences, track_number=track_number, disc_number=disc_number)

    def is_already_downloaded(self, track: TrackMetadata) -> Optional[str]:
        """Return canonical file path if the track already exists in the library.

        In smart_dedup mode (default): checks the global identity index first
        (ISRC, Spotify ID, title+artist keys) — finds the track anywhere in the
        library regardless of which album folder it lives in.

        In full_albums mode: skips the cross-library index and only checks
        whether a file already exists at the exact target path for this track.
        This lets the same track appear in multiple album folders (e.g. studio
        album and a Best Of compilation) without one blocking the other.
        """
        if self.filename_preferences.get("filename_conflict_behavior") == "skip" and not self.full_albums:
            for key in self._track_identity_keys(track):
                for existing in tuple(self._identity_candidates.get(key, ())):
                    if not os.path.exists(existing):
                        continue
                    if key in self._identity_keys_for_path(Path(existing)):
                        return existing

        # Check the expected canonical path for this exact request.
        base = self.get_output_path(track)
        for ext in SUPPORTED_AUDIO_EXTENSIONS:
            candidate = base + ext
            if os.path.exists(candidate):
                if self.filename_preferences.get("filename_conflict_behavior") == "overwrite":
                    return None
                if not self._file_matches_track_identity(track, Path(candidate)):
                    continue
                self._mark_done(track, candidate)
                return candidate

        return None

    def has_exact_output(self, track: TrackMetadata) -> bool:
        """Return whether this album/playlist request already has its own file."""
        base = self.get_output_path(track)
        return any(
            os.path.exists(base + ext)
            and self._file_matches_track_identity(track, Path(base + ext))
            for ext in SUPPORTED_AUDIO_EXTENSIONS
        )

    def mark_downloaded(self, track: TrackMetadata, file_path: str):
        self._mark_done(track, file_path)

    def mark_failed(self, track: TrackMetadata, reason: str):
        with self._state_lock:
            for key in self._track_identity_keys(track):
                state_key = f"{FAILED_PREFIX}{key}"
                self._state[state_key] = reason
                self._pending_state_updates[state_key] = reason
            self._pending_mutations += 1
            self._save_state()

    def flush(self) -> bool:
        """Synchronously persist pending completed-state and identity updates."""
        with self._state_lock:
            if self._persistence_timer is not None:
                self._persistence_timer.cancel()
                self._persistence_timer = None
            success = self._flush_persistence_locked()
            if not success and (
                self._pending_state_updates or self._identity_cache_is_dirty()
            ):
                self._schedule_persistence_locked()
            return success

    def ensure_request_copy(self, track: TrackMetadata, canonical_path: str) -> str:
        """Materialize a matching local file in this request's exact folder.

        Hard links avoid storing the audio twice where the filesystem supports
        them; a normal copy is the portable fallback.
        """
        if not canonical_path or not os.path.exists(canonical_path):
            return canonical_path

        ext = Path(canonical_path).suffix
        request_path = self.get_output_path(track) + ext
        if os.path.abspath(request_path) == os.path.abspath(canonical_path):
            return canonical_path
        if os.path.exists(request_path):
            if self._file_matches_track_identity(track, Path(request_path)):
                return request_path
            request_path = str(self._resolve_conflict_path(
                Path(request_path).parent, Path(request_path).stem, track=track
            )) + ext

        os.makedirs(os.path.dirname(os.path.abspath(request_path)), exist_ok=True)
        try:
            os.link(canonical_path, request_path)
        except OSError:
            shutil.copy2(canonical_path, request_path)
        self._mark_done(track, request_path)
        return request_path

    # Compatibility for callers outside the engine.
    ensure_playlist_copy = ensure_request_copy

    def write_playlist_manifest(self, playlist_name: str, file_paths: list[str]) -> str:
        manifest_root = self.root if self.playlist_folder_structure == "flat" else self.playlists_root
        manifest_path = manifest_root / f"{self._safe(playlist_name)}.m3u"
        lines = ["#EXTM3U"]
        for file_path in file_paths:
            if not file_path:
                continue
            relative = os.path.relpath(file_path, manifest_path.parent)
            lines.append(Path(relative).as_posix())
        manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(manifest_path)

    def _resolve_conflict_path(self, folder: Path, stem: str, track: Optional[TrackMetadata] = None) -> Path:
        behavior = self.filename_preferences.get("filename_conflict_behavior")
        if behavior != "append_counter":
            if not track:
                return folder / stem
            existing_paths = [
                folder / f"{stem}{ext}"
                for ext in SUPPORTED_AUDIO_EXTENSIONS
                if (folder / f"{stem}{ext}").exists()
            ]
            if not existing_paths:
                return folder / stem
            if any(self._file_matches_track_identity(track, path) for path in existing_paths):
                return folder / stem
            counter = 2
            while any((folder / f"{stem} ({counter}){ext}").exists() for ext in SUPPORTED_AUDIO_EXTENSIONS):
                counter += 1
            return folder / f"{stem} ({counter})"

        candidate = folder / stem
        if not any((folder / f"{stem}{ext}").exists() for ext in SUPPORTED_AUDIO_EXTENSIONS):
            return candidate
        counter = 2
        while any((folder / f"{stem} ({counter}){ext}").exists() for ext in SUPPORTED_AUDIO_EXTENSIONS):
            counter += 1
        return folder / f"{stem} ({counter})"

    # ── State / identity helpers ──────────────────────────────────────────

    def _mark_done(self, track: TrackMetadata, path: str):
        resolved = str(Path(path).resolve())
        identity_keys = self._track_identity_keys(track)
        with self._state_lock:
            for key in identity_keys:
                state_key = f"{TRACK_KEY_PREFIX}{key}"
                self._state[state_key] = resolved
                self._pending_state_updates[state_key] = resolved
                self._add_identity_candidate(key, resolved, prefer=True)
            self._upsert_completed_identity_record(Path(resolved), identity_keys)
            self._pending_mutations += 1
            self._save_state()

    def _track_identity_keys(self, track: TrackMetadata) -> list[str]:
        keys: list[str] = []
        if track.isrc:
            keys.append(f"isrc:{track.isrc.strip().lower()}")
        if track.spotify_id:
            keys.append(f"spotify:{track.spotify_id.strip()}")

        title_key = self._normalize_identity_part(track.title)
        artist_key = self._normalize_identity_part(track.primary_artist)
        album_key = self._normalize_identity_part(track.album)

        if title_key and artist_key:
            keys.append(f"title_artist:{title_key}:{artist_key}")
        if title_key and artist_key and album_key and album_key != "unknown album":
            keys.append(f"title_artist_album:{title_key}:{artist_key}:{album_key}")

        # Source-independent all-artists key: splits combined strings like
        # "Future & Metro Boomin", normalizes each part, sorts them — so
        # ["Future", "Metro Boomin"], ["Metro Boomin", "Future"], and
        # ["Future & Metro Boomin"] (single combined tag) all produce the same key.
        if track.artists:
            canonical = self._artists_canonical_key(track.artists)
            if title_key and canonical:
                keys.append(f"title_artists:{title_key}:{canonical}")

        return list(dict.fromkeys(keys))

    def _build_identity_index(self):
        cache_valid, old_records = self._load_identity_cache_records()
        scan_errors: list[OSError] = []
        new_records: dict[str, dict[str, Any]] = {}
        reusable_by_file_id: dict[tuple[int, int, int, int], list[dict[str, Any]]] = {}
        if cache_valid:
            for record in old_records.values():
                file_id = self._record_file_id(record)
                if file_id is not None and record.get("complete") is True:
                    reusable_by_file_id.setdefault(file_id, []).append(record)

        for file_path in self._iter_audio_files(scan_errors):
            try:
                before = file_path.stat()
            except OSError as exc:
                scan_errors.append(exc)
                continue
            path_key = self._normalized_path(file_path)
            old_record = old_records.get(path_key)
            if (
                cache_valid
                and old_record is not None
                and old_record.get("complete") is True
                and self._record_matches_stat(old_record, before)
            ):
                new_records[path_key] = dict(old_record)
                continue

            reused_record: Optional[dict[str, Any]] = None
            if cache_valid:
                file_id = self._stat_file_id(before)
                candidates = reusable_by_file_id.get(file_id, ()) if file_id is not None else ()
                if candidates:
                    reused_keys = sorted({
                        key
                        for candidate in candidates
                        for key in candidate.get("keys", ())
                        if isinstance(key, str)
                    })
                    reused_record = self._identity_record(
                        file_path,
                        before,
                        reused_keys,
                        complete=True,
                    )

            record = reused_record or self._probe_identity_record(file_path, before)
            if record is not None:
                new_records[path_key] = record

        if scan_errors and cache_valid:
            # A permission/transient enumeration failure is not evidence that a
            # previously indexed file was deleted.
            for path_key, record in old_records.items():
                new_records.setdefault(path_key, record)

        self._merge_completed_state_identities(new_records)
        self._identity_records = new_records
        self._identity_cache_complete = not scan_errors
        self._rebuild_identity_lookup()

        if not cache_valid:
            self._identity_force_rewrite = True
            self._identity_pending_upserts = dict(new_records)
        else:
            for path_key, record in new_records.items():
                if old_records.get(path_key) != record:
                    self._identity_pending_upserts[path_key] = record
            if not scan_errors:
                for path_key, record in old_records.items():
                    if path_key not in new_records:
                        self._identity_pending_deletions[path_key] = record

        if self._identity_cache_is_dirty():
            with self._state_lock:
                if not self._flush_identity_cache_locked():
                    self._schedule_persistence_locked()

    def _load_identity_records_without_scan(self):
        cache_valid, records = self._load_identity_cache_records()
        if cache_valid:
            self._identity_records = records
            self._rebuild_identity_lookup()

    def _iter_audio_files(self, errors: list[OSError]) -> Iterator[Path]:
        def record_error(error: OSError):
            errors.append(error)

        for directory, child_directories, filenames in os.walk(
            self.root,
            topdown=True,
            onerror=record_error,
            followlinks=False,
        ):
            child_directories.sort(key=str.casefold)
            filenames.sort(key=str.casefold)
            parent = Path(directory)
            for filename in filenames:
                if Path(filename).suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                    yield parent / filename

    def _load_identity_cache_records(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        payload = _read_json_object(self._identity_cache_path)
        valid, records, complete = self._validate_identity_cache_payload(payload)
        if not valid or not complete:
            return False, {}
        return True, records

    def _validate_identity_cache_payload(
        self,
        payload: Optional[dict[str, Any]],
    ) -> tuple[bool, dict[str, dict[str, Any]], bool]:
        if not payload:
            return False, {}, False
        if payload.get("schema_version") != IDENTITY_INDEX_SCHEMA_VERSION:
            return False, {}, False
        if payload.get("root") != self._normalized_path(self.root):
            return False, {}, False
        if not isinstance(payload.get("complete"), bool):
            return False, {}, False
        raw_records = payload.get("records")
        if not isinstance(raw_records, dict):
            return False, {}, False

        records: dict[str, dict[str, Any]] = {}
        for path_key, value in raw_records.items():
            if not isinstance(path_key, str) or not isinstance(value, dict):
                return False, {}, False
            path = value.get("path")
            keys = value.get("keys")
            if not isinstance(path, str) or self._normalized_path(path) != path_key:
                return False, {}, False
            if type(value.get("size")) is not int or value["size"] < 0:
                return False, {}, False
            if type(value.get("mtime_ns")) is not int or value["mtime_ns"] < 0:
                return False, {}, False
            if not isinstance(value.get("complete"), bool):
                return False, {}, False
            if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
                return False, {}, False
            for field in ("device", "inode"):
                if value.get(field) is not None and type(value[field]) is not int:
                    return False, {}, False
            records[path_key] = {
                "path": path,
                "size": value["size"],
                "mtime_ns": value["mtime_ns"],
                "device": value.get("device"),
                "inode": value.get("inode"),
                "keys": list(dict.fromkeys(keys)),
                "complete": value["complete"],
            }
        return True, records, payload["complete"]

    @staticmethod
    def _stat_file_id(stat_result: os.stat_result) -> Optional[tuple[int, int, int, int]]:
        inode = int(getattr(stat_result, "st_ino", 0) or 0)
        if inode == 0:
            return None
        return (
            int(getattr(stat_result, "st_dev", 0) or 0),
            inode,
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
        )

    @staticmethod
    def _record_file_id(record: dict[str, Any]) -> Optional[tuple[int, int, int, int]]:
        inode = record.get("inode")
        if not isinstance(inode, int) or inode == 0:
            return None
        return (
            int(record.get("device") or 0),
            inode,
            int(record["size"]),
            int(record["mtime_ns"]),
        )

    @staticmethod
    def _record_matches_stat(record: dict[str, Any], stat_result: os.stat_result) -> bool:
        return (
            record.get("size") == int(stat_result.st_size)
            and record.get("mtime_ns") == int(stat_result.st_mtime_ns)
        )

    def _identity_record(
        self,
        path: Path,
        stat_result: os.stat_result,
        keys: list[str],
        *,
        complete: bool,
    ) -> dict[str, Any]:
        inode = int(getattr(stat_result, "st_ino", 0) or 0)
        return {
            "path": os.path.abspath(os.path.normpath(os.fspath(path))),
            "size": int(stat_result.st_size),
            "mtime_ns": int(stat_result.st_mtime_ns),
            "device": int(getattr(stat_result, "st_dev", 0) or 0) if inode else None,
            "inode": inode or None,
            "keys": list(dict.fromkeys(keys)),
            "complete": complete,
        }

    def _probe_identity_record(
        self,
        path: Path,
        before: os.stat_result,
    ) -> Optional[dict[str, Any]]:
        try:
            keys = self._extract_identity_keys_from_file(path)
        except Exception as exc:
            logger.debug("Library scan could not probe %s: %s", path, exc)
            return self._identity_record(path, before, [], complete=False)
        try:
            after = path.stat()
        except OSError:
            return None
        if not self._record_matches_stat(self._identity_record(path, before, [], complete=True), after):
            logger.debug("Library file changed while indexing: %s", path)
            return self._identity_record(path, after, [], complete=False)
        return self._identity_record(path, after, keys, complete=True)

    def _merge_completed_state_identities(
        self,
        records: dict[str, dict[str, Any]],
    ):
        state_keys_by_path: dict[str, list[str]] = {}
        for key, path in self._load_track_entries_from_state().items():
            state_keys_by_path.setdefault(self._normalized_path(path), []).append(key)

        for path_key, state_keys in state_keys_by_path.items():
            record = records.get(path_key)
            if not record or record.get("complete") is not True:
                continue
            probed_keys = list(record.get("keys", ()))
            if not self._state_keys_match_record(state_keys, probed_keys):
                continue
            record["keys"] = list(dict.fromkeys([*probed_keys, *state_keys]))

    @classmethod
    def _state_keys_match_record(cls, state_keys: list[str], record_keys: list[str]) -> bool:
        if set(state_keys).intersection(record_keys):
            return True
        # Filename-only formats produce a weak title key. It is safe to use
        # that title to migrate richer completed-state keys, but a tagged file
        # with a different artist/ISRC must not inherit stale state merely
        # because its title happens to match.
        if not record_keys or any(not key.startswith("title:") for key in record_keys):
            return False
        record_titles = {
            title
            for key in record_keys
            if (title := cls._identity_key_title(key))
        }
        return any(cls._identity_key_title(key) in record_titles for key in state_keys)

    @staticmethod
    def _identity_key_title(key: str) -> str:
        for prefix in ("title_artist_album:", "title_artists:", "title_artist:", "title:"):
            if key.startswith(prefix):
                return key[len(prefix):].split(":", 1)[0]
        return ""

    @staticmethod
    def _normalized_path(path: os.PathLike[str] | str) -> str:
        return os.path.normcase(os.path.abspath(os.path.normpath(os.fspath(path))))

    def _rebuild_identity_lookup(self):
        self._identity_index.clear()
        self._identity_candidates.clear()
        for path_key in sorted(self._identity_records):
            record = self._identity_records[path_key]
            if record.get("complete") is not True:
                continue
            for key in record.get("keys", ()):
                self._add_identity_candidate(key, record["path"])

    def _add_identity_candidate(self, key: str, path: str, *, prefer: bool = False):
        candidates = self._identity_candidates.setdefault(key, [])
        candidates[:] = [candidate for candidate in candidates if candidate != path]
        if prefer:
            candidates.insert(0, path)
        else:
            candidates.append(path)
        if candidates:
            self._identity_index[key] = candidates[0]

    def _remove_identity_candidate(self, key: str, path: str):
        candidates = self._identity_candidates.get(key)
        if not candidates:
            return
        candidates[:] = [candidate for candidate in candidates if candidate != path]
        if candidates:
            self._identity_index[key] = candidates[0]
        else:
            self._identity_candidates.pop(key, None)
            self._identity_index.pop(key, None)

    def _replace_identity_record(self, path_key: str, record: dict[str, Any]):
        old_record = self._identity_records.get(path_key)
        if old_record:
            for key in old_record.get("keys", ()):
                self._remove_identity_candidate(key, old_record["path"])
        self._identity_records[path_key] = record
        for key in record.get("keys", ()):
            self._add_identity_candidate(key, record["path"])
        self._identity_pending_upserts[path_key] = record
        self._identity_pending_deletions.pop(path_key, None)

    def _delete_identity_record(self, path_key: str):
        old_record = self._identity_records.pop(path_key, None)
        if not old_record:
            return
        for key in old_record.get("keys", ()):
            self._remove_identity_candidate(key, old_record["path"])
        self._identity_pending_upserts.pop(path_key, None)
        self._identity_pending_deletions[path_key] = old_record

    def _upsert_completed_identity_record(self, path: Path, keys: list[str]):
        try:
            stat_result = path.stat()
        except OSError:
            return
        path_key = self._normalized_path(path)
        old_record = self._identity_records.get(path_key)
        if old_record and self._record_matches_stat(old_record, stat_result):
            keys = list(dict.fromkeys([*old_record.get("keys", ()), *keys]))
        record = self._identity_record(path, stat_result, keys, complete=True)
        if old_record != record:
            self._replace_identity_record(path_key, record)

    def _identity_keys_for_path(self, path: Path) -> list[str]:
        path_key = self._normalized_path(path)
        with self._state_lock:
            try:
                before = path.stat()
            except OSError:
                self._delete_identity_record(path_key)
                self._schedule_persistence_locked()
                return []
            record = self._identity_records.get(path_key)
            if (
                record is not None
                and record.get("complete") is True
                and self._record_matches_stat(record, before)
            ):
                return list(record.get("keys", ()))

            file_id = self._stat_file_id(before)
            reusable = [
                candidate
                for candidate in self._identity_records.values()
                if candidate.get("complete") is True
                and self._record_file_id(candidate) == file_id
            ] if file_id is not None else []
            if reusable:
                keys = sorted({
                    key
                    for candidate in reusable
                    for key in candidate.get("keys", ())
                })
                new_record = self._identity_record(path, before, keys, complete=True)
            else:
                new_record = self._probe_identity_record(path, before)
            if new_record is None:
                self._delete_identity_record(path_key)
                self._schedule_persistence_locked()
                return []
            self._replace_identity_record(path_key, new_record)
            self._schedule_persistence_locked()
            return list(new_record.get("keys", ())) if new_record.get("complete") else []

    def _load_track_entries_from_state(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for key, value in self._state.items():
            if not isinstance(value, str):
                continue
            if key.startswith(FAILED_PREFIX):
                continue
            if key.startswith(TRACK_KEY_PREFIX):
                entries[key[len(TRACK_KEY_PREFIX):]] = value
                continue

            # Legacy state support
            canonical_key = self._legacy_state_key_to_identity(key)
            if canonical_key:
                entries[canonical_key] = value
        return entries

    @staticmethod
    def _legacy_state_key_to_identity(key: str) -> Optional[str]:
        if key.startswith("playlist:"):
            if ":spotify:" in key:
                return f"spotify:{key.split(':spotify:', 1)[1]}"
            return None
        if key.startswith("isrc:") or key.startswith("spotify:") or key.startswith("title:"):
            return key
        return None

    def _load_state(self) -> dict:
        return _read_json_object(self._state_path) or {}

    def _save_state(self):
        with self._state_lock:
            if self._pending_mutations >= _PERSIST_BATCH_MUTATIONS:
                if self._persistence_timer is not None:
                    self._persistence_timer.cancel()
                    self._persistence_timer = None
                state_ok = self._flush_state_locked()
                if state_ok:
                    self._pending_mutations = 0
                if (
                    not state_ok
                    or self._pending_state_updates
                    or self._identity_cache_is_dirty()
                ):
                    self._schedule_persistence_locked()
            else:
                self._schedule_persistence_locked()

    def _schedule_persistence_locked(self):
        if self._persistence_timer is not None:
            return
        timer = threading.Timer(_PERSIST_DELAY_SECONDS, self._flush_persistence_from_timer)
        # A short non-daemon debounce guarantees the final partial batch is
        # persisted before a one-job backend process exits normally.
        timer.daemon = False
        self._persistence_timer = timer
        timer.start()

    def _flush_persistence_from_timer(self):
        with self._state_lock:
            self._persistence_timer = None
            self._flush_persistence_locked()

    def _flush_persistence_locked(self) -> bool:
        state_ok = self._flush_state_locked()
        identity_ok = self._flush_identity_cache_locked()
        if state_ok:
            self._pending_mutations = 0
        return state_ok and identity_ok

    def _flush_state_locked(self) -> bool:
        if not self._pending_state_updates:
            return True
        updates = dict(self._pending_state_updates)
        lock_path = self._state_path.with_name(f"{self._state_path.name}.lock")
        with _exclusive_file_lock(lock_path) as acquired:
            if not acquired:
                return False
            disk_state = _read_json_object(self._state_path) or {}
            disk_state.update(updates)
            if not _atomic_replace_json(self._state_path, disk_state):
                return False
        for key, value in updates.items():
            if self._pending_state_updates.get(key) == value:
                self._pending_state_updates.pop(key, None)
        self._state = {**disk_state, **self._pending_state_updates}
        return True

    def _identity_cache_is_dirty(self) -> bool:
        return bool(
            self._identity_force_rewrite
            or self._identity_pending_upserts
            or self._identity_pending_deletions
        )

    @staticmethod
    def _same_record_fingerprint(
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> bool:
        return (
            first.get("size") == second.get("size")
            and first.get("mtime_ns") == second.get("mtime_ns")
            and first.get("device") == second.get("device")
            and first.get("inode") == second.get("inode")
        )

    def _flush_identity_cache_locked(self) -> bool:
        if not self._identity_cache_is_dirty():
            return True
        pending_upserts = dict(self._identity_pending_upserts)
        pending_deletions = dict(self._identity_pending_deletions)
        force_rewrite = self._identity_force_rewrite
        lock_path = self._identity_cache_path.with_name(
            f"{self._identity_cache_path.name}.lock"
        )
        with _exclusive_file_lock(lock_path) as acquired:
            if not acquired:
                return False
            disk_payload = _read_json_object(self._identity_cache_path)
            disk_valid, disk_records, disk_complete = self._validate_identity_cache_payload(
                disk_payload
            )
            if disk_valid and (not force_rewrite or disk_complete):
                merged_records = disk_records
            else:
                merged_records = dict(self._identity_records)

            for path_key, expected_record in pending_deletions.items():
                current_record = merged_records.get(path_key)
                if (
                    current_record is not None
                    and self._same_record_fingerprint(current_record, expected_record)
                ):
                    merged_records.pop(path_key, None)
            for path_key, record in pending_upserts.items():
                try:
                    current_stat = Path(record["path"]).stat()
                except OSError:
                    continue
                if self._record_matches_stat(record, current_stat):
                    merged_records[path_key] = record

            complete = self._identity_cache_complete
            if disk_valid and not force_rewrite:
                complete = complete and disk_complete
            payload = {
                "schema_version": IDENTITY_INDEX_SCHEMA_VERSION,
                "root": self._normalized_path(self.root),
                "complete": complete,
                "generated_ns": time.time_ns(),
                "records": merged_records,
            }
            if not _atomic_replace_json(self._identity_cache_path, payload):
                return False

        for path_key, record in pending_upserts.items():
            if self._identity_pending_upserts.get(path_key) == record:
                self._identity_pending_upserts.pop(path_key, None)
        for path_key, record in pending_deletions.items():
            if self._identity_pending_deletions.get(path_key) == record:
                self._identity_pending_deletions.pop(path_key, None)
        self._identity_force_rewrite = False
        return True

    # ── File scan helpers ─────────────────────────────────────────────────

    def _extract_identity_keys_from_file(self, path: Path) -> list[str]:
        ext = path.suffix.lower()
        if ext == ".flac":
            return self._extract_flac_identity_keys(path)
        if ext == ".mp3":
            return self._extract_mp3_identity_keys(path)
        if ext in {".m4a", ".mp4"}:
            return self._extract_mp4_identity_keys(path)
        return self._extract_filename_identity_keys(path)

    def _file_matches_track_identity(self, track: TrackMetadata, path: Path) -> bool:
        """Return True only when an existing file is the same logical track.

        A path collision alone is not enough to skip. Playlists can contain
        distinct covers with the same title, and title-only filename templates
        render those tracks to the same stem.
        """
        existing_keys = set(self._identity_keys_for_path(path))
        return bool(existing_keys.intersection(self._track_identity_keys(track)))

    def _extract_flac_identity_keys(self, path: Path) -> list[str]:
        audio = FLAC(path)
        title = self._first(audio.get("title"))
        artists = audio.get("artist", [])
        album = self._first(audio.get("album"))
        isrc = self._first(audio.get("isrc"))
        spotify_id = self._first(audio.get("spotify_id"))
        return self._identity_keys_from_values(title, artists, album, isrc, spotify_id)

    def _extract_mp3_identity_keys(self, path: Path) -> list[str]:
        audio = ID3(path)
        title = self._id3_text(audio.getall("TIT2"))
        artists = self._id3_text_list(audio.getall("TPE1"))
        album = self._id3_text(audio.getall("TALB"))
        isrc = self._id3_text(audio.getall("TSRC"))
        spotify_id = self._id3_txxx(audio, "SPOTIFYID")
        return self._identity_keys_from_values(title, artists, album, isrc, spotify_id)

    def _extract_mp4_identity_keys(self, path: Path) -> list[str]:
        audio = MP4(path)
        title = self._first(audio.get("\xa9nam"))
        artists = [value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
                   for value in audio.get("\xa9ART", [])]
        album = self._first(audio.get("\xa9alb"))
        isrc = self._first_freeform(audio, "ISRC")
        spotify_id = self._first_freeform(audio, "SPOTIFYID")
        return self._identity_keys_from_values(title, artists, album, isrc, spotify_id)

    def _extract_filename_identity_keys(self, path: Path) -> list[str]:
        stem = path.stem
        stem = re.sub(r"^\d+\s*-\s*", "", stem).strip()
        title = stem or "Unknown Track"
        return self._identity_keys_from_values(title, [], None, None, None)

    def _identity_keys_from_values(
        self,
        title: Optional[str],
        artists: list[str],
        album: Optional[str],
        isrc: Optional[str],
        spotify_id: Optional[str],
    ) -> list[str]:
        keys: list[str] = []
        if isrc:
            keys.append(f"isrc:{isrc.strip().lower()}")
        if spotify_id:
            keys.append(f"spotify:{spotify_id.strip()}")

        title_key = self._normalize_identity_part(title)
        artist_key = self._normalize_identity_part(artists[0] if artists else None)
        album_key = self._normalize_identity_part(album)

        if title_key and artist_key:
            keys.append(f"title_artist:{title_key}:{artist_key}")
        if title_key and artist_key and album_key and album_key != "unknown album":
            keys.append(f"title_artist_album:{title_key}:{artist_key}:{album_key}")
        elif title_key:
            keys.append(f"title:{title_key}")

        # All-artists key: same logic as _track_identity_keys.
        # When the file has a combined tag like artist = ["Future & Metro Boomin"],
        # _artists_canonical_key splits it to the same key as ["Future", "Metro Boomin"].
        if artists:
            canonical = self._artists_canonical_key(artists)
            if title_key and canonical:
                keys.append(f"title_artists:{title_key}:{canonical}")

        return list(dict.fromkeys(keys))

    @staticmethod
    def _first(values) -> Optional[str]:
        if not values:
            return None
        value = values[0]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    @staticmethod
    def _id3_text(frames) -> Optional[str]:
        if not frames:
            return None
        texts = getattr(frames[0], "text", None) or []
        return str(texts[0]) if texts else None

    @staticmethod
    def _id3_text_list(frames) -> list[str]:
        if not frames:
            return []
        texts = getattr(frames[0], "text", None) or []
        return [str(text) for text in texts if text]

    @staticmethod
    def _id3_txxx(audio: ID3, desc: str) -> Optional[str]:
        for frame in audio.getall("TXXX"):
            if getattr(frame, "desc", "") == desc:
                texts = getattr(frame, "text", None) or []
                return str(texts[0]) if texts else None
        return None

    @staticmethod
    def _first_freeform(audio: MP4, desc: str) -> Optional[str]:
        key = f"----:com.apple.iTunes:{desc}"
        values = audio.get(key, [])
        if not values:
            return None
        value = values[0]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    @staticmethod
    def _normalize_identity_part(value: Optional[str]) -> str:
        if not value:
            return ""
        value = value.lower()
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"[^a-z0-9 ]+", "", value)
        return value.strip()

    @staticmethod
    def _artists_canonical_key(artists: list[str]) -> str:
        """Return a source-independent sorted key for a set of artists.

        Splits combined strings (e.g. "Future & Metro Boomin") into individual
        names before normalizing and sorting, so different ways of expressing the
        same collaboration all produce the same key:
          ["Future", "Metro Boomin"]     → "future metro boomin"
          ["Metro Boomin", "Future"]     → "future metro boomin"
          ["Future & Metro Boomin"]      → "future metro boomin"
          ["Future", "Metro Boomin & X"] → "future metro boomin x"
        """
        parts: set[str] = set()
        for artist in artists:
            # Split on common multi-artist separators found in tags
            for part in re.split(r"[,&/]+|\s+(?:feat\.?|ft\.?)\s+", artist, flags=re.IGNORECASE):
                norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", part.lower())).strip()
                if norm:
                    parts.add(norm)
        return " ".join(sorted(parts))

    # ── Path sanitization ─────────────────────────────────────────────────

    @staticmethod
    def _safe(name: str, max_len: int = 80) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
        name = name.strip(". ")
        return name[:max_len] or "Unknown"
