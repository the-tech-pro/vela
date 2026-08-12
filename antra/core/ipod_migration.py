"""Immutable backup-to-sync staging for replacement iPod migration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

MIGRATION_BUNDLE_SCHEMA_VERSION = 1
MAX_MIGRATION_TRACKS = 100_000
MAX_MIGRATION_PLAYLISTS = 10_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROTECTED_MEDIA_EXTENSIONS = frozenset({".aa", ".aax", ".m4p"})
_PARSER_COMPANION_NAMES = frozenset({
    "itunesdb",
    "itunescdb",
    "play counts",
    "preferences",
})
_TRACK_FIELDS = frozenset({
    "Title",
    "Artist",
    "Album",
    "Album Artist",
    "Genre",
    "Composer",
    "Comment",
    "Grouping",
    "Sort Artist",
    "Sort Title",
    "Sort Name",
    "Sort Album",
    "Sort Album Artist",
    "Sort Composer",
    "Show",
    "Episode",
    "Description Text",
    "Long Description",
    "TV Network",
    "Sort Show",
    "Subtitle",
    "Category",
    "Podcast RSS URL",
    "Podcast Enclosure URL",
    "Lyrics",
    "track_id",
    "db_track_id",
    "db_id",
    "size",
    "length",
    "bitrate",
    "sample_rate_1",
    "vbr_flag",
    "year",
    "track_number",
    "total_tracks",
    "disc_number",
    "total_discs",
    "bpm",
    "rating",
    "play_count_1",
    "skip_count",
    "last_played",
    "last_skipped",
    "bookmark_time",
    "date_added",
    "date_released",
    "compilation_flag",
    "sound_check",
    "pregap",
    "postgap",
    "sample_count",
    "gapless_audio_payload_size",
    "gapless_track_flag",
    "explicit_flag",
    "lyrics_flag",
    "media_type",
    "movie_flag",
    "season_number",
    "episode_number",
    "podcast_flag",
    "use_podcast_now_playing_flag",
    "chapter_data",
})


class MigrationBundleError(RuntimeError):
    """Raised when a backup cannot become a bounded immutable sync bundle."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = _bounded_json(details or {})


def build_migration_bundle(
    entries: Mapping[str, Mapping[str, Any]],
    backup_root: str | Path,
    bundle_dir: str | Path,
    *,
    snapshot_fingerprint: str,
    progress: Callable[[str, int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Materialize verified media and a source-database metadata sidecar."""

    from iopenpod.itunesdb_parser.ipod_library import load_ipod_library
    from iopenpod.sync._formats import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
    from iopenpod.sync.ipod_track_paths import _device_relative_track_location

    root = Path(backup_root)
    destination = Path(bundle_dir)
    if destination.exists():
        raise MigrationBundleError(
            "migration_bundle_exists",
            "The migration staging bundle already exists.",
        )
    normalized_entries = _validated_entries(entries)
    database_relative = _database_entry(normalized_entries)
    temporary = destination.with_name(
        f".{destination.name}.{secrets.token_hex(6)}.tmp"
    )
    metadata_root = temporary / ".source-metadata"
    media_root = temporary / "media"
    playlist_root = temporary / "playlists"
    try:
        metadata_root.mkdir(parents=True, exist_ok=False)
        _materialize_parser_files(
            normalized_entries,
            root,
            metadata_root,
            progress=progress,
            cancelled=cancelled,
        )
        database_path = metadata_root.joinpath(*database_relative.parts)
        library = load_ipod_library(str(database_path), merge_playcounts=True)
        if not isinstance(library, Mapping):
            raise MigrationBundleError(
                "source_database_unreadable",
                "iOpenPod could not parse the selected backup database.",
            )
        source_tracks = list(library.get("mhlt", []) or [])
        if not source_tracks:
            raise MigrationBundleError(
                "source_library_empty",
                "The selected snapshot has no migration-capable media tracks.",
            )
        if len(source_tracks) > MAX_MIGRATION_TRACKS:
            raise MigrationBundleError(
                "source_library_too_large",
                "The selected snapshot exceeds the migration track safety limit.",
            )

        media_root.mkdir(parents=True, exist_ok=True)
        staged_tracks: list[dict[str, Any]] = []
        source_track_paths: dict[int, str] = {}
        source_db_paths: dict[int, str] = {}
        skipped_unresolved = 0
        protected: list[str] = []
        supported_extensions = frozenset(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
        for index, track in enumerate(source_tracks, start=1):
            _check_cancelled(cancelled)
            raw_location = str(
                track.get("Location") or track.get("location") or ""
            ).strip()
            relative_location = _device_relative_track_location(raw_location)
            if relative_location is None:
                skipped_unresolved += 1
                continue
            source_relative = _safe_manifest_path(relative_location)
            entry = normalized_entries.get(source_relative.as_posix().casefold())
            if entry is None:
                skipped_unresolved += 1
                continue
            extension = source_relative.suffix.casefold()
            if extension in _PROTECTED_MEDIA_EXTENSIONS:
                protected.append(source_relative.name)
                continue
            if extension not in supported_extensions:
                skipped_unresolved += 1
                continue
            staged_name = (
                f"{index:06d}-{entry['hash'][:20]}{extension}"
            )
            staged_path = media_root / staged_name
            _copy_verified_blob(
                root,
                entry,
                staged_path,
                cancelled=cancelled,
            )
            metadata = _track_metadata(track)
            staged_relative = staged_path.relative_to(temporary).as_posix()
            staged_tracks.append({
                "staged_path": staged_relative,
                "source_relative_path": source_relative.as_posix(),
                "blob_sha256": entry["hash"],
                "size": entry["size"],
                "metadata": metadata,
            })
            track_identity = _positive_int(track.get("track_id"))
            if track_identity:
                source_track_paths.setdefault(
                    track_identity,
                    staged_relative,
                )
            db_identity = _positive_int(
                track.get("db_track_id", track.get("db_id"))
            )
            if db_identity:
                source_db_paths.setdefault(db_identity, staged_relative)
            if progress:
                progress(
                    "staging_media",
                    index,
                    len(source_tracks),
                    f"Staged {source_relative.name}",
                )

        if protected:
            raise MigrationBundleError(
                "protected_media_unsupported",
                "Protected Audible or FairPlay media cannot be proven portable "
                "to a replacement iPod.",
                details={"examples": protected[:10], "count": len(protected)},
            )
        if not staged_tracks:
            raise MigrationBundleError(
                "source_media_unavailable",
                "No source database tracks resolve to verified backup media blobs.",
            )

        playlist_root.mkdir(parents=True, exist_ok=True)
        staged_playlists, skipped_playlists, unresolved_playlist_items = (
            _stage_standard_playlists(
                library,
                temporary,
                playlist_root,
                source_track_paths,
                source_db_paths,
            )
        )
        payload = {
            "schema_version": MIGRATION_BUNDLE_SCHEMA_VERSION,
            "snapshot_fingerprint": str(snapshot_fingerprint).casefold(),
            "tracks": staged_tracks,
            "playlists": staged_playlists,
            "media_file_count": len(staged_tracks),
            "playlist_count": len(staged_playlists),
            "total_media_bytes": sum(
                int(track["size"]) for track in staged_tracks
            ),
            "limitations": {
                "preserved": [
                    "track descriptive metadata",
                    "ratings",
                    "play counts",
                    "standard playlist membership and order",
                    "podcast, audiobook, video, gapless, chapter, and lyric flags "
                    "when represented by iOpenPod PCTrack",
                ],
                "not_preserved": [
                    "source hardware identity and volume files",
                    "source iTunesDB, SQLiteDB, artwork databases, and checksums",
                    "artwork and photos",
                    "smart-playlist rules and system playlists",
                    "skip counts, last-played/last-skipped times, bookmarks, "
                    "date-added values, and store/application identifiers",
                ],
                "unresolved_source_tracks": skipped_unresolved,
                "skipped_playlists": skipped_playlists,
                "unresolved_playlist_items": unresolved_playlist_items,
            },
        }
        payload["bundle_fingerprint"] = _payload_fingerprint(payload)
        _atomic_json(temporary / "bundle.json", payload)
        shutil.rmtree(metadata_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        return load_migration_bundle(destination, verify_media=True)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_migration_bundle(
    bundle_dir: str | Path,
    *,
    verify_media: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Load a bounded bundle and optionally re-hash every staged media file."""

    root = Path(bundle_dir)
    try:
        payload = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationBundleError(
            "migration_bundle_missing",
            "The reviewed migration staging bundle was not found.",
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != MIGRATION_BUNDLE_SCHEMA_VERSION
        or payload.get("bundle_fingerprint") != _payload_fingerprint(payload)
    ):
        raise MigrationBundleError(
            "migration_bundle_invalid",
            "The reviewed migration staging bundle is invalid.",
        )
    tracks = payload.get("tracks")
    playlists = payload.get("playlists")
    if (
        not isinstance(tracks, list)
        or not isinstance(playlists, list)
        or not tracks
        or len(tracks) > MAX_MIGRATION_TRACKS
        or len(playlists) > MAX_MIGRATION_PLAYLISTS
    ):
        raise MigrationBundleError(
            "migration_bundle_invalid",
            "The reviewed migration staging bundle is invalid.",
        )
    for item in [*tracks, *playlists]:
        if not isinstance(item, Mapping):
            raise MigrationBundleError(
                "migration_bundle_invalid",
                "The reviewed migration staging bundle is invalid.",
            )
        relative = _safe_bundle_path(str(item.get("staged_path", "")))
        path = root.joinpath(*relative.parts)
        _require_regular_contained_file(root, path)
    for playlist in playlists:
        expected_hash = str(playlist.get("sha256", "")).casefold()
        expected_size = int(playlist.get("size", -1))
        path = root.joinpath(
            *_safe_bundle_path(str(playlist["staged_path"])).parts
        )
        actual_size, actual_hash = _hash_file(path, cancelled=cancelled)
        if (
            not _SHA256_RE.fullmatch(expected_hash)
            or actual_size != expected_size
            or actual_hash != expected_hash
        ):
            raise MigrationBundleError(
                "migration_bundle_changed",
                "A staged migration playlist changed after review.",
            )
    if verify_media:
        for track in tracks:
            _check_cancelled(cancelled)
            expected_hash = str(track.get("blob_sha256", "")).casefold()
            expected_size = int(track.get("size", -1))
            path = root.joinpath(
                *_safe_bundle_path(str(track["staged_path"])).parts
            )
            actual_size, actual_hash = _hash_file(path, cancelled=cancelled)
            if actual_size != expected_size or actual_hash != expected_hash:
                raise MigrationBundleError(
                    "migration_bundle_changed",
                    "A staged migration media file changed after review.",
                )
    return payload


def migration_source_files(
    bundle_dir: str | Path,
    bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    root = Path(bundle_dir)
    return tuple(
        str(root.joinpath(*_safe_bundle_path(str(track["staged_path"])).parts))
        for track in bundle["tracks"]
    )


def compute_iopenpod_migration_plan(
    device: Any,
    bundle_dir: str | Path,
    bundle: Mapping[str, Any],
    ipod_tracks: Iterable[dict[str, Any]],
    existing_playlists: Iterable[dict[str, Any]],
    *,
    progress: Callable[[Any], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Any:
    """Use iOpenPod's pinned planner with source-DB metadata-backed PCTracks."""

    from iopenpod.device.capabilities import capabilities_for_family_gen
    from iopenpod.sync.fingerprint_diff_engine import FingerprintDiffEngine

    library = _MigrationPCLibrary(Path(bundle_dir), bundle)
    capabilities = capabilities_for_family_gen(
        str(getattr(device, "model_family", "") or ""),
        str(getattr(device, "generation", "") or ""),
        capacity=str(getattr(device, "capacity", "") or "") or None,
        model_number=str(getattr(device, "model_number", "") or "") or None,
    )
    if capabilities is None:
        raise MigrationBundleError(
            "target_capabilities_unknown",
            "iOpenPod has no pinned capability profile for the replacement iPod.",
        )

    def on_progress(stage: str, current: int, total: int, message: str) -> None:
        if progress:
            progress(
                type(
                    "MigrationProgress",
                    (),
                    {
                        "stage": stage,
                        "current": current,
                        "total": total,
                        "message": message,
                    },
                )()
            )

    planner = FingerprintDiffEngine(
        library,
        device.path,
        supports_video=bool(capabilities.supports_video),
        supports_podcast=bool(capabilities.supports_podcast),
        supports_photo=False,
    )
    plan = planner.compute_diff(
        list(ipod_tracks),
        progress_callback=on_progress,
        write_fingerprints=False,
        is_cancelled=cancelled,
        allowed_paths=frozenset(library.source_files),
        selected_playlist_paths=frozenset(library.playlist_files),
        existing_playlists=list(existing_playlists),
    )
    exact_titles = {
        os.path.normcase(os.path.abspath(path)): title
        for path, title in library.playlist_titles.items()
    }
    for attribute in ("playlists_to_add", "playlists_to_edit"):
        for playlist in list(getattr(plan, attribute, ()) or ()):
            source_path = str(playlist.get("_sync_playlist_path", "") or "")
            title = exact_titles.get(os.path.normcase(os.path.abspath(source_path)))
            if title:
                playlist["Title"] = title
    return plan


class _MigrationPCLibrary:
    """Minimal SourceLibrary implementation backed by the immutable sidecar."""

    def __init__(self, root: Path, bundle: Mapping[str, Any]) -> None:
        from iopenpod.infrastructure.media_folders import (
            MEDIA_TYPE_MUSIC,
            MEDIA_TYPE_PLAYLISTS,
            MEDIA_TYPE_VIDEO,
            MediaFolderEntry,
        )

        self.root_path = root
        self.root_entries = (
            MediaFolderEntry(
                directory=str(root),
                recurse=True,
                media_types=(
                    MEDIA_TYPE_MUSIC,
                    MEDIA_TYPE_VIDEO,
                    MEDIA_TYPE_PLAYLISTS,
                ),
            ),
        )
        self._tracks = tuple(bundle["tracks"])
        self._playlists = tuple(bundle["playlists"])
        self.source_files = tuple(
            str(root.joinpath(*_safe_bundle_path(str(item["staged_path"])).parts))
            for item in self._tracks
        )
        self.playlist_files = tuple(
            str(root.joinpath(*_safe_bundle_path(str(item["staged_path"])).parts))
            for item in self._playlists
        )
        self.playlist_titles = {
            path: str(item.get("title", "") or "Imported Playlist")
            for path, item in zip(
                self.playlist_files,
                self._playlists,
                strict=True,
            )
        }

    def scan(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        include_video: bool = True,
        max_workers: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[Any]:
        del max_workers
        total = len(self._tracks)
        for index, item in enumerate(self._tracks, start=1):
            if is_cancelled and is_cancelled():
                return
            track = _pc_track(self.root_path, item)
            if track.is_video and not include_video:
                continue
            if progress_callback:
                progress_callback(index, total, track.filename)
            yield track


def _pc_track(root: Path, item: Mapping[str, Any]) -> Any:
    from iopenpod.itunesdb_shared.constants import (
        MEDIA_TYPE_AUDIOBOOK,
        MEDIA_TYPE_MUSIC_VIDEO,
        MEDIA_TYPE_PODCAST,
        MEDIA_TYPE_TV_SHOW,
        MEDIA_TYPE_VIDEO,
        MEDIA_TYPE_VIDEO_PODCAST,
    )
    from iopenpod.sync._formats import NEEDS_TRANSCODING, VIDEO_EXTENSIONS
    from iopenpod.sync.pc_library import PCTrack

    path = root.joinpath(*_safe_bundle_path(str(item["staged_path"])).parts)
    metadata = item.get("metadata")
    if not isinstance(metadata, Mapping):
        raise MigrationBundleError(
            "migration_bundle_invalid",
            "A migration metadata sidecar entry is invalid.",
        )
    media_type = _positive_int(metadata.get("media_type"))
    extension = path.suffix.casefold()
    is_video = extension in VIDEO_EXTENSIONS
    is_podcast = bool(
        media_type & (MEDIA_TYPE_PODCAST | MEDIA_TYPE_VIDEO_PODCAST)
        or metadata.get("podcast_flag")
        or metadata.get("use_podcast_now_playing_flag")
    )
    is_audiobook = bool(media_type & MEDIA_TYPE_AUDIOBOOK)
    video_kind = ""
    if media_type & MEDIA_TYPE_TV_SHOW:
        video_kind = "tv_show"
    elif media_type & MEDIA_TYPE_MUSIC_VIDEO:
        video_kind = "music_video"
    elif media_type & (MEDIA_TYPE_VIDEO | MEDIA_TYPE_VIDEO_PODCAST):
        video_kind = "movie"
    chapters = metadata.get("chapter_data")
    if isinstance(chapters, Mapping):
        chapters = chapters.get("chapters")
    if not isinstance(chapters, list):
        chapters = None
    file_stat = path.stat()
    return PCTrack(
        path=str(path),
        relative_path=str(item.get("source_relative_path", path.name)),
        filename=path.name,
        extension=extension,
        mtime=file_stat.st_mtime,
        size=file_stat.st_size,
        title=_text(metadata, "Title", path.stem),
        artist=_text(metadata, "Artist", "Unknown Artist"),
        album=_text(metadata, "Album", "Unknown Album"),
        album_artist=_optional_text(metadata, "Album Artist"),
        genre=_optional_text(metadata, "Genre"),
        year=_optional_int(metadata.get("year")),
        track_number=_optional_int(metadata.get("track_number")),
        track_total=_optional_int(metadata.get("total_tracks")),
        disc_number=_optional_int(metadata.get("disc_number")),
        disc_total=_optional_int(metadata.get("total_discs")),
        duration_ms=max(0, int(metadata.get("length", 0) or 0)),
        bitrate=_optional_int(metadata.get("bitrate")),
        sample_rate=_optional_int(metadata.get("sample_rate_1")),
        rating=_optional_int(metadata.get("rating")),
        sort_artist=_optional_text(metadata, "Sort Artist"),
        sort_name=_optional_text(
            metadata,
            "Sort Title",
            _optional_text(metadata, "Sort Name"),
        ),
        sort_album=_optional_text(metadata, "Sort Album"),
        sort_album_artist=_optional_text(metadata, "Sort Album Artist"),
        sort_composer=_optional_text(metadata, "Sort Composer"),
        compilation=bool(metadata.get("compilation_flag", 0)),
        comment=_optional_text(metadata, "Comment"),
        composer=_optional_text(metadata, "Composer"),
        grouping=_optional_text(metadata, "Grouping"),
        bpm=_optional_int(metadata.get("bpm")),
        sound_check=max(0, int(metadata.get("sound_check", 0) or 0)),
        pregap=max(0, int(metadata.get("pregap", 0) or 0)),
        postgap=max(0, int(metadata.get("postgap", 0) or 0)),
        sample_count=max(0, int(metadata.get("sample_count", 0) or 0)),
        gapless_data=max(
            0,
            int(metadata.get("gapless_audio_payload_size", 0) or 0),
        ),
        gapless_track_flag=max(
            0,
            int(metadata.get("gapless_track_flag", 0) or 0),
        ),
        vbr=bool(metadata.get("vbr_flag", 0)),
        play_count=max(0, int(metadata.get("play_count_1", 0) or 0)),
        date_released=max(0, int(metadata.get("date_released", 0) or 0)),
        subtitle=_optional_text(metadata, "Subtitle"),
        explicit_flag=max(0, int(metadata.get("explicit_flag", 0) or 0)),
        has_lyrics=bool(
            metadata.get("lyrics_flag", 0) or metadata.get("Lyrics")
        ),
        lyrics=_optional_text(metadata, "Lyrics"),
        art_hash=None,
        is_video=is_video,
        video_kind=video_kind,
        show_name=_optional_text(metadata, "Show"),
        season_number=_optional_int(metadata.get("season_number")),
        episode_number=_optional_int(metadata.get("episode_number")),
        episode_id=_optional_text(metadata, "Episode"),
        description=_optional_text(metadata, "Description Text"),
        long_description=_optional_text(metadata, "Long Description"),
        network_name=_optional_text(metadata, "TV Network"),
        sort_show=_optional_text(metadata, "Sort Show"),
        is_podcast=is_podcast,
        is_audiobook=is_audiobook,
        category=_optional_text(metadata, "Category"),
        podcast_url=_optional_text(metadata, "Podcast RSS URL"),
        podcast_enclosure_url=_optional_text(
            metadata,
            "Podcast Enclosure URL",
        ),
        chapters=chapters,
        needs_transcoding=bool(
            extension in NEEDS_TRANSCODING or is_video
        ),
    )


def _validated_entries(
    entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(entries, Mapping) or not entries:
        raise MigrationBundleError(
            "backup_manifest_invalid",
            "The selected snapshot has no validated backup entries.",
        )
    result: dict[str, dict[str, Any]] = {}
    for raw_path, raw_info in entries.items():
        path = _safe_manifest_path(str(raw_path))
        if not isinstance(raw_info, Mapping):
            raise MigrationBundleError(
                "backup_manifest_invalid",
                "A backup manifest file entry is invalid.",
            )
        file_hash = str(raw_info.get("hash", "")).casefold()
        size = int(raw_info.get("size", -1))
        if not _SHA256_RE.fullmatch(file_hash) or size < 0:
            raise MigrationBundleError(
                "backup_manifest_invalid",
                "A backup manifest blob reference is invalid.",
            )
        key = path.as_posix().casefold()
        if key in result:
            raise MigrationBundleError(
                "backup_manifest_invalid",
                "The backup manifest contains case-colliding paths.",
            )
        result[key] = {
            "path": path,
            "hash": file_hash,
            "size": size,
        }
    return result


def _database_entry(entries: Mapping[str, Mapping[str, Any]]) -> PurePosixPath:
    for candidate in (
        "ipod_control/itunes/itunescdb",
        "ipod_control/itunes/itunesdb",
    ):
        entry = entries.get(candidate)
        if entry is not None and int(entry["size"]) > 0:
            return entry["path"]
    raise MigrationBundleError(
        "source_database_missing",
        "The selected snapshot does not contain a non-empty iTunesDB/iTunesCDB.",
    )


def _materialize_parser_files(
    entries: Mapping[str, Mapping[str, Any]],
    backup_root: Path,
    metadata_root: Path,
    *,
    progress: Callable[[str, int, int, str], None] | None,
    cancelled: Callable[[], bool] | None,
) -> None:
    selected = [
        entry
        for entry in entries.values()
        if _is_parser_companion(entry["path"])
    ]
    for index, entry in enumerate(selected, start=1):
        _check_cancelled(cancelled)
        destination = metadata_root.joinpath(*entry["path"].parts)
        _copy_verified_blob(
            backup_root,
            entry,
            destination,
            cancelled=cancelled,
        )
        if progress:
            progress(
                "parsing_source",
                index,
                len(selected),
                f"Validated {entry['path'].name}",
            )


def _is_parser_companion(path: PurePosixPath) -> bool:
    casefolded = tuple(part.casefold() for part in path.parts)
    if casefolded == ("ipod_control", "device", "preferences"):
        return True
    if len(casefolded) < 3 or casefolded[:2] != ("ipod_control", "itunes"):
        return False
    name = casefolded[-1]
    return name in _PARSER_COMPANION_NAMES or name.startswith("otgplaylistinfo")


def _stage_standard_playlists(
    library: Mapping[str, Any],
    bundle_root: Path,
    playlist_root: Path,
    source_track_paths: Mapping[int, str],
    source_db_paths: Mapping[int, str],
) -> tuple[list[dict[str, Any]], int, int]:
    source_playlists = list(library.get("mhlp", []) or [])
    staged: list[dict[str, Any]] = []
    skipped = 0
    unresolved_items = 0
    for index, playlist in enumerate(source_playlists, start=1):
        if len(staged) >= MAX_MIGRATION_PLAYLISTS:
            raise MigrationBundleError(
                "source_library_too_large",
                "The selected snapshot exceeds the migration playlist safety limit.",
            )
        if not isinstance(playlist, Mapping) or playlist.get("master_flag"):
            skipped += 1
            continue
        title = str(playlist.get("Title", "") or "Imported Playlist")[:300]
        paths: list[str] = []
        for item in list(playlist.get("items", []) or []):
            if not isinstance(item, Mapping):
                unresolved_items += 1
                continue
            source_path = source_track_paths.get(
                _positive_int(item.get("track_id"))
            )
            if source_path is None:
                source_path = source_db_paths.get(
                    _positive_int(
                        item.get("db_track_id", item.get("db_id"))
                    )
                )
            if source_path is None:
                unresolved_items += 1
                continue
            paths.append(str(bundle_root.joinpath(*PurePosixPath(source_path).parts)))
        filename = f"{index:06d}.m3u8"
        playlist_path = playlist_root / filename
        playlist_path.write_text(
            "#EXTM3U\n" + "".join(f"{path}\n" for path in paths),
            encoding="utf-8",
            newline="\n",
        )
        playlist_size, playlist_hash = _hash_file(
            playlist_path,
            cancelled=None,
        )
        staged.append({
            "staged_path": playlist_path.relative_to(bundle_root).as_posix(),
            "title": title,
            "source_playlist_id": _positive_int(playlist.get("playlist_id")),
            "item_count": len(paths),
            "size": playlist_size,
            "sha256": playlist_hash,
        })
    return staged, skipped, unresolved_items


def _copy_verified_blob(
    backup_root: Path,
    entry: Mapping[str, Any],
    destination: Path,
    *,
    cancelled: Callable[[], bool] | None,
) -> None:
    file_hash = str(entry["hash"])
    source = backup_root / "blobs" / file_hash[:2] / file_hash
    try:
        source_stat = source.lstat()
    except OSError as exc:
        raise MigrationBundleError(
            "backup_blob_missing",
            "A referenced backup blob is unavailable.",
        ) from exc
    if (
        stat.S_ISLNK(source_stat.st_mode)
        or not stat.S_ISREG(source_stat.st_mode)
        or source_stat.st_size != int(entry["size"])
    ):
        raise MigrationBundleError(
            "backup_blob_invalid",
            "A referenced backup blob is not a regular file of the expected size.",
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while True:
            _check_cancelled(cancelled)
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if copied != int(entry["size"]) or digest.hexdigest() != file_hash:
        destination.unlink(missing_ok=True)
        raise MigrationBundleError(
            "backup_blob_corrupt",
            "A referenced backup blob failed SHA-256 verification.",
        )


def _safe_manifest_path(value: str) -> PurePosixPath:
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise MigrationBundleError(
            "unsafe_backup_path",
            "The backup manifest contains an unsafe path.",
        )
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} or ":" in part for part in path.parts):
        raise MigrationBundleError(
            "unsafe_backup_path",
            "The backup manifest contains an unsafe path.",
        )
    return path


def _safe_bundle_path(value: str) -> PurePosixPath:
    path = _safe_manifest_path(value)
    if path.parts[0] not in {"media", "playlists"}:
        raise MigrationBundleError(
            "migration_bundle_invalid",
            "The migration bundle references a file outside its staging roots.",
        )
    return path


def _require_regular_contained_file(root: Path, path: Path) -> None:
    try:
        root_resolved = root.resolve(strict=True)
        path_resolved = path.resolve(strict=True)
        file_stat = path.lstat()
    except OSError as exc:
        raise MigrationBundleError(
            "migration_bundle_missing",
            "A migration staging file is missing.",
        ) from exc
    if (
        path_resolved == root_resolved
        or root_resolved not in path_resolved.parents
        or stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
    ):
        raise MigrationBundleError(
            "migration_bundle_invalid",
            "A migration staging path is unsafe.",
        )


def _track_metadata(track: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _bounded_json(track[key])
        for key in _TRACK_FIELDS
        if key in track
    }


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key != "bundle_fingerprint"
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _hash_file(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            _check_cancelled(cancelled)
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise MigrationBundleError(
            "cancelled",
            "The replacement migration was cancelled before writing.",
        )


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return str(value)[:2_000]
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_json(item, depth=depth + 1) for item in value[:1_000]]
    if isinstance(value, str):
        return value[:20_000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:2_000]


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    number = _positive_int(value)
    return number or None


def _text(
    metadata: Mapping[str, Any],
    key: str,
    default: str | None = None,
) -> str:
    return str(metadata.get(key, "") or default or "")[:2_000]


def _optional_text(
    metadata: Mapping[str, Any],
    key: str,
    default: str | None = None,
) -> str | None:
    value = _text(metadata, key, default)
    return value or None
