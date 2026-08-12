"""
Apple Music user-library client for the desktop My Library screen.

Uses the same Apple Music web session that Antra already captures:
  - Authorization: Bearer ...
  - Music-User-Token: ...

The public Catalog API is enough for shared URLs, but the user's private
library requires the authenticated MusicKit "me/library" endpoints.
"""

from __future__ import annotations

import logging
import hashlib
import json
import re
import sqlite3
import threading
import time
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import requests

from antra.core.models import TrackMetadata

logger = logging.getLogger(__name__)

APPLE_LIBRARY_SONGS_URL = "apple-music://library/songs"
APPLE_LIBRARY_PLAYLIST_URL_PREFIX = "apple-music://library/playlist/"
APPLE_LIBRARY_ALBUM_URL_PREFIX = "apple-music://library/album/"
_LIBRARY_API_BASE = "https://api.music.apple.com/v1/me/library"
_APPLE_API_BASE = "https://api.music.apple.com"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36"
)

_ALGO_NAMES = re.compile(
    r"favorites mix|new music mix|chill mix|friends mix|replay|personal station"
    r"|made for you|mix$|mix \d|station",
    re.IGNORECASE,
)


def is_apple_library_url(url: str) -> bool:
    return (
        url.startswith(APPLE_LIBRARY_SONGS_URL)
        or url.startswith(APPLE_LIBRARY_PLAYLIST_URL_PREFIX)
        or url.startswith(APPLE_LIBRARY_ALBUM_URL_PREFIX)
    )


def extract_apple_library_playlist_id(url: str) -> Optional[str]:
    if not url.startswith(APPLE_LIBRARY_PLAYLIST_URL_PREFIX):
        return None
    playlist_id = url[len(APPLE_LIBRARY_PLAYLIST_URL_PREFIX):].strip()
    return playlist_id or None


def extract_apple_library_album_id(url: str) -> Optional[str]:
    if not url.startswith(APPLE_LIBRARY_ALBUM_URL_PREFIX):
        return None
    album_id = url[len(APPLE_LIBRARY_ALBUM_URL_PREFIX):].strip()
    return album_id or None


class _AppleLibraryCache:
    """Small persistent cache for the user's Apple Music library index."""

    def __init__(self, path: Optional[str], namespace: str):
        self.path = Path(path).resolve() if path else None
        self.namespace = namespace
        self._lock = threading.RLock()
        self._db: Optional[sqlite3.Connection] = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._db = sqlite3.connect(
                    str(self.path),
                    timeout=5,
                    check_same_thread=False,
                )
                with self._lock:
                    db = self._connection()
                    db.execute("PRAGMA busy_timeout = 5000")
                    db.execute("PRAGMA journal_mode = WAL")
                    db.execute("PRAGMA synchronous = NORMAL")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS apple_cache ("
                        "cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
                    )
                    db.commit()
            except Exception:
                self.close()
                raise

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Apple Music cache is closed.")
        return self._db

    def close(self) -> None:
        """Close the shared connection so Windows can remove the cache files."""
        with self._lock:
            db, self._db = self._db, None
            if db is not None:
                db.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _rollback_quietly(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.rollback()
                except Exception:
                    pass

    def get(self, key: str):
        if not self.path:
            return None
        try:
            with self._lock:
                db = self._connection()
                row = db.execute(
                    "SELECT payload, updated_at FROM apple_cache WHERE cache_key = ?",
                    (f"{self.namespace}:{key}",),
                ).fetchone()
            if not row:
                return None
            payload = json.loads(row[0])
            if isinstance(payload, dict):
                payload.setdefault("cached_at", row[1])
            return payload
        except Exception as exc:
            logger.debug("[AppleLibrary] cache read failed: %s", exc)
            return None

    def set(self, key: str, payload):
        if not self.path:
            return
        try:
            now = time.time()
            with self._lock:
                db = self._connection()
                db.execute(
                    "INSERT INTO apple_cache(cache_key, payload, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (f"{self.namespace}:{key}", json.dumps(payload, ensure_ascii=False), now),
                )
                db.commit()
        except Exception as exc:
            self._rollback_quietly()
            logger.debug("[AppleLibrary] cache write failed: %s", exc)

    def values_with_prefix(self, prefix: str) -> list:
        if not self.path:
            return []
        try:
            with self._lock:
                db = self._connection()
                rows = db.execute(
                    "SELECT payload FROM apple_cache WHERE cache_key LIKE ?",
                    (f"{self.namespace}:{prefix}%",),
                ).fetchall()
            return [json.loads(row[0]) for row in rows]
        except Exception as exc:
            logger.debug("[AppleLibrary] cache prefix read failed: %s", exc)
            return []

    def remove_values_with_prefix_except(self, prefix: str, suffixes: set[str]) -> None:
        if not self.path:
            return
        try:
            full_prefix = f"{self.namespace}:{prefix}"
            keep = {f"{full_prefix}{suffix}" for suffix in suffixes}
            with self._lock:
                db = self._connection()
                rows = db.execute(
                    "SELECT cache_key FROM apple_cache WHERE cache_key LIKE ?",
                    (f"{full_prefix}%",),
                ).fetchall()
                stale = [(row[0],) for row in rows if row[0] not in keep]
                if stale:
                    db.executemany("DELETE FROM apple_cache WHERE cache_key = ?", stale)
                    db.commit()
        except Exception as exc:
            self._rollback_quietly()
            logger.debug("[AppleLibrary] stale cache cleanup failed: %s", exc)

    def replace_values_with_prefix(self, prefix: str, payloads: dict, item_callback=None) -> None:
        """Atomically replace a group without one giant JSON cache value."""
        if not self.path:
            return
        try:
            now = time.time()
            full_prefix = f"{self.namespace}:{prefix}"
            with self._lock:
                db = self._connection()
                db.execute("DELETE FROM apple_cache WHERE cache_key LIKE ?", (f"{full_prefix}%",))
                for suffix, payload in payloads.items():
                    db.execute(
                        "INSERT INTO apple_cache(cache_key, payload, updated_at) VALUES(?, ?, ?) ",
                        (f"{full_prefix}{suffix}", json.dumps(payload, ensure_ascii=False), now),
                    )
                    if item_callback:
                        item_callback()
                db.commit()
        except Exception as exc:
            self._rollback_quietly()
            logger.debug("[AppleLibrary] grouped cache write failed: %s", exc)
            raise


class AppleLibraryClient:
    """Fetch a user's Apple Music library (saved songs + library playlists)."""

    _ALBUM_SUMMARY_FIELDS = (
        "id",
        "name",
        "url",
        "image_url",
        "track_count",
        "track_count_known",
        "artist_name",
        "release_date",
    )
    _PLAYLIST_SUMMARY_FIELDS = (
        "id",
        "name",
        "url",
        "image_url",
        "track_count",
        "track_count_known",
        "owner_name",
        "is_algorithmic",
        "description",
        "global_id",
    )
    _VOLATILE_REVISION_FIELDS = frozenset({
        "cached_at",
        "from_cache",
        "indexed_at",
        "revision",
    })

    def __init__(
        self,
        authorization_token: str,
        music_user_token: str,
        storefront: str = "gb",
        cache_path: Optional[str] = None,
        cache_only: bool = False,
    ):
        auth = (authorization_token or "").strip()
        mut = (music_user_token or "").strip()
        if not auth and not cache_only:
            raise ValueError("Apple Music authorization token is required.")
        if not mut and not cache_only:
            raise ValueError("Apple Music user token is required.")

        self._auth = auth
        self._mut = mut
        self._cache_only = bool(cache_only)
        self._storefront = (storefront or "gb").strip().lower() or "gb"
        namespace = hashlib.sha256(mut.encode("utf-8")).hexdigest()[:16]
        self._cache = _AppleLibraryCache(cache_path, namespace)
        self._session = requests.Session()
        headers = {
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "User-Agent": _UA,
        }
        if self._auth:
            headers["Authorization"] = self._auth
        if self._mut:
            headers["Music-User-Token"] = self._mut
        self._session.headers.update(headers)

    def close(self) -> None:
        self._cache.close()
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def __del__(self):
        try:
            cache = getattr(self, "_cache", None)
            if cache is not None:
                cache.close()
            session = getattr(self, "_session", None)
            if session is not None:
                session.close()
        except Exception:
            pass

    def get_library(self, force_refresh: bool = False, include_cached_views: bool = True) -> dict:
        if not force_refresh:
            cached = self._cache.get("library-index-v2")
            if self._is_valid_library_summary(cached):
                result = self._compact_library_summary(cached, from_cache=True)
                return self._attach_cached_views(result) if include_cached_views else result
        saved_songs_count = self._get_saved_songs_count()
        albums = self._get_all_albums()
        playlists = self._get_all_playlists()
        result = {
            "saved_songs_count": saved_songs_count,
            "albums": albums,
            "playlists": playlists,
            "indexed_at": time.time(),
            "from_cache": False,
        }
        result = self._compact_library_summary(result, from_cache=False)
        self._cache.set("library-index-v2", result)
        return self._attach_cached_views(result) if include_cached_views else result

    @staticmethod
    def _is_valid_library_summary(payload) -> bool:
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("albums"), list)
            and isinstance(payload.get("playlists"), list)
            and isinstance(payload.get("saved_songs_count"), int)
        )

    def _compact_library_summary(self, library: dict, from_cache: bool) -> dict:
        albums = [
            {
                field: item.get(field)
                for field in self._ALBUM_SUMMARY_FIELDS
                if field in item
            }
            for item in (library.get("albums") or [])
            if isinstance(item, dict)
        ]
        playlists = [
            {
                field: item.get(field)
                for field in self._PLAYLIST_SUMMARY_FIELDS
                if field in item
            }
            for item in (library.get("playlists") or [])
            if isinstance(item, dict)
        ]
        result = {
            "saved_songs_count": max(0, int(library.get("saved_songs_count") or 0)),
            "albums": albums,
            "playlists": playlists,
            "indexed_at": library.get("indexed_at") or library.get("cached_at"),
            "from_cache": bool(from_cache),
        }
        result["summary_revision"] = self._stable_revision({
            "saved_songs_count": result["saved_songs_count"],
            "albums": albums,
            "playlists": playlists,
        })
        return result

    def _attach_cached_views(self, library: dict) -> dict:
        """Attach compact cached metadata without bulk release-track graphs."""
        result = dict(library)
        for key in ("albums", "playlists"):
            resized = []
            for item in result.get(key) or []:
                resized.append({
                    **item,
                    "image_url": self._resize_cached_artwork_url(item.get("image_url"), 316),
                })
            result[key] = resized
        artist_index = self._cache.get("artist-index-v2") or self._cache.get("artist-index-v1") or {}
        state = self._cache.get("full-index-state-v4") or {}
        result["artists"] = [
            {
                "name": artist.get("name") or "",
                "image_url": self._resize_cached_artwork_url(artist.get("image_url"), 316),
                "track_count": max(0, int(artist.get("track_count") or 0)),
            }
            for artist in artist_index.get("artists") or []
            if isinstance(artist, dict) and artist.get("name")
        ]
        index_complete = self._index_state_matches_library(result, state)
        state_total = max(0, int(state.get("total") or 0))
        state_completed = max(0, min(state_total, int(state.get("completed") or 0)))
        state_percent = 100 if index_complete else min(
            99,
            max(
                0,
                int(
                    state.get("percent")
                    or ((state_completed / state_total) * 100 if state_total else 0)
                ),
            ),
        )
        index_revision = str(
            state.get("revision")
            or (
                self._stable_revision({
                    "complete": state.get("complete") is True,
                    "target_signature": state.get("target_signature") or [],
                })
                if state
                else ""
            )
        )
        result["index_complete"] = index_complete
        result["index_progress"] = {
            "completed": state_completed,
            "total": state_total,
            "percent": state_percent,
            "release_completed": max(0, int(state.get("release_completed") or 0)),
            "release_total": max(0, int(state.get("release_total") or 0)),
            "error_count": len(state.get("errors") or []),
        }
        result["index_indexed_at"] = state.get("indexed_at")
        result["index_revision"] = index_revision
        result["revision"] = self._stable_revision({
            "summary_revision": result["summary_revision"],
            "artists": result["artists"],
            "index_complete": index_complete,
            "index_progress": result["index_progress"],
            "index_revision": index_revision,
        })
        return result

    def _index_state_matches_library(self, library: dict, state: dict) -> bool:
        if not isinstance(state, dict) or state.get("complete") is not True:
            return False
        expected_urls = {
            str(item.get("url") or "")
            for group in ("albums", "playlists")
            for item in (library.get(group) or [])
            if item.get("url")
        }
        if int(library.get("saved_songs_count") or 0) > 0:
            expected_urls.add(APPLE_LIBRARY_SONGS_URL)
        state_urls = {
            str(url)
            for url in (
                state.get("target_urls")
                or [item[0] for item in (state.get("target_signature") or []) if item]
            )
        }
        if expected_urls != state_urls:
            return False

        state_counts = {
            str(item[0]): max(0, int(item[1] or 0))
            for item in (state.get("target_signature") or [])
            if isinstance(item, (list, tuple)) and len(item) >= 2
        }
        known_counts = {}
        for group in ("albums", "playlists"):
            for item in library.get(group) or []:
                if item.get("track_count_known") is True and item.get("url"):
                    known_counts[str(item["url"])] = max(0, int(item.get("track_count") or 0))
        saved_count = max(0, int(library.get("saved_songs_count") or 0))
        if saved_count:
            known_counts[APPLE_LIBRARY_SONGS_URL] = saved_count
        return all(state_counts.get(url) == count for url, count in known_counts.items())

    @classmethod
    def _stable_revision(cls, payload) -> str:
        def stable_value(value):
            if isinstance(value, dict):
                return {
                    str(key): stable_value(item)
                    for key, item in value.items()
                    if key not in cls._VOLATILE_REVISION_FIELDS
                }
            if isinstance(value, (list, tuple)):
                return [stable_value(item) for item in value]
            return value

        encoded = json.dumps(
            stable_value(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    @staticmethod
    def _resize_cached_artwork_url(url: Optional[str], max_px: int) -> Optional[str]:
        if not url:
            return url
        return re.sub(
            r"/\d+x\d+([a-zA-Z]{0,6}\.)",
            rf"/{max_px}x{max_px}\1",
            str(url),
            count=1,
        )

    def index_entire_library(self, progress_callback=None, force_refresh_summary: bool = True) -> dict:
        """Checkpoint every release and its tracks into the local SQLite index."""
        library = self.get_library(force_refresh=force_refresh_summary, include_cached_views=False)
        targets = [
            {
                "url": item["url"],
                "label": item["name"],
                "track_total": max(0, int(item.get("track_count") or 0)),
                "track_count_known": bool(
                    item.get("track_count_known", int(item.get("track_count") or 0) > 0)
                ),
            }
            for item in library.get("albums", []) if item.get("url")
        ]
        targets.extend(
            {
                "url": item["url"],
                "label": item["name"],
                "track_total": max(0, int(item.get("track_count") or 0)),
                "track_count_known": bool(
                    item.get("track_count_known", int(item.get("track_count") or 0) > 0)
                ),
            }
            for item in library.get("playlists", []) if item.get("url")
        )
        saved_count = max(0, int(library.get("saved_songs_count") or 0))
        if saved_count:
            targets.append({
                "url": APPLE_LIBRARY_SONGS_URL,
                "label": "Library Songs",
                "track_total": saved_count,
                "track_count_known": True,
            })

        # Apple frequently omits trackCount from playlist summaries. Resolve all
        # missing totals up front from meta.total so the denominator represents
        # the actual work instead of treating a 2,000-song playlist as one item.
        if progress_callback:
            progress_callback({
                "completed": 0, "total": 1, "percent": 0,
                "release_completed": 0, "release_total": len(targets),
                "label": "Counting library songs",
            })
        missing_counts = [
            target for target in targets
            if target["track_total"] <= 0 and not target["track_count_known"]
        ]
        if missing_counts:
            with ThreadPoolExecutor(max_workers=min(4, len(missing_counts))) as pool:
                futures = {pool.submit(self._get_target_track_count, target["url"]): target for target in missing_counts}
                for future in as_completed(futures):
                    target = futures[future]
                    try:
                        target["track_total"] = max(0, int(future.result() or 0))
                    except Exception as exc:
                        logger.debug("[AppleLibrary] count lookup failed for %s: %s", target["url"], exc)

        release_total = len(targets)
        release_completed = 0
        # Every release song is counted once when fetched/checkpointed and once
        # when folded into instant artist views. Final validation is explicit.
        song_total = sum(target["track_total"] for target in targets)
        total = release_total + (song_total * 2) + 1
        completed = 0
        target_by_url = {target["url"]: target for target in targets}
        track_progress = {target["url"]: 0 for target in targets}
        indexed_details: dict[str, dict] = {}
        progress_lock = threading.Lock()
        target_urls = sorted(target_by_url)
        # Lists survive the JSON/SQLite cache round-trip unchanged. Tuples would
        # come back as lists and force an unnecessary full re-index every launch.
        target_signature = sorted(
            [[target["url"], target["track_total"]] for target in targets],
            key=lambda item: item[0],
        )
        previous_state = self._cache.get("full-index-state-v4")
        if (
            isinstance(previous_state, dict)
            and previous_state.get("complete") is True
            and previous_state.get("target_signature") == target_signature
        ):
            return {**previous_state, "already_complete": True}

        def report(label: str):
            if progress_callback:
                progress_callback({
                    "completed": completed,
                    "total": total,
                    # Whole percentages are stable in the compact sidebar. 100
                    # remains reserved for the explicit, validated completion.
                    "percent": min(99, int((completed / total) * 100)) if total else 0,
                    "release_completed": release_completed,
                    "release_total": release_total,
                    "label": label,
                })

        def report_track_progress(url: str, label: str, count: int, actual_total: Optional[int] = None):
            nonlocal completed, total
            with progress_lock:
                target = target_by_url[url]
                if actual_total is not None:
                    actual_total = max(0, int(actual_total))
                    if actual_total != target["track_total"]:
                        total += (actual_total - target["track_total"]) * 2
                        target["track_total"] = actual_total
                weight = target["track_total"]
                bounded = min(weight, max(0, int(count or 0)))
                previous = track_progress.get(url, 0)
                if bounded <= previous:
                    return
                track_progress[url] = bounded
                completed += bounded - previous
                report(label)

        report("Preparing library index")
        pending = list(targets)
        final_errors = []
        for attempt in range(3):
            if not pending:
                break
            failed = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(
                        self.get_playlist_detail,
                        target["url"],
                        False,
                        lambda count, actual_total=None, target_url=target["url"], target_label=target["label"]:
                            report_track_progress(target_url, target_label, count, actual_total),
                    ): target
                    for target in pending
                }
                for future in as_completed(futures):
                    target = futures[future]
                    url, label = target["url"], target["label"]
                    try:
                        detail = future.result()
                    except Exception as exc:
                        with progress_lock:
                            completed -= track_progress.get(url, 0)
                            track_progress[url] = 0
                            report(f"Retrying {label}")
                        failed.append((target, str(exc)))
                    else:
                        with progress_lock:
                            actual_count = len(detail.get("tracks") or [])
                            indexed_details[url] = detail
                            if actual_count != target["track_total"]:
                                total += (actual_count - target["track_total"]) * 2
                                target["track_total"] = actual_count
                            weight = target["track_total"]
                            remaining_tracks = weight - track_progress.get(url, 0)
                            track_progress[url] = weight
                            release_completed += 1
                            completed += remaining_tracks + 1
                            report(label)
            pending = [target for target, _ in failed]
            final_errors = [{"url": target["url"], "message": message} for target, message in failed]
            if pending and attempt < 2:
                time.sleep(0.75 * (attempt + 1))

        # Artist pages are a secondary view of the same indexed track records;
        # materialize that lookup before validation so artist opens are local-only.
        artist_phase_total = song_total
        if not final_errors:
            self._cache.remove_values_with_prefix_except("detail-v2:", set(target_urls))
            artist_progress = 0
            artist_keys = {
                part.strip().casefold()
                for detail in indexed_details.values()
                for track in (detail.get("tracks") or [])
                for part in re.split(r"[,;&]", str(track.get("artist") or ""))
                if part.strip()
            }
            artist_phase_total = song_total + len(artist_keys)
            total += len(artist_keys)

            def report_artist_progress(count: int, actual_total: Optional[int] = None):
                nonlocal completed, total, artist_progress, artist_phase_total
                if actual_total is not None and actual_total != artist_phase_total:
                    total += int(actual_total) - artist_phase_total
                    artist_phase_total = int(actual_total)
                count = max(artist_progress, int(count or 0))
                completed += count - artist_progress
                artist_progress = count
                report("Building artist index")

            report("Building artist index")
            self._build_artist_index(list(indexed_details.values()), report_artist_progress)

        validation_errors = []
        if not final_errors:
            for target in targets:
                detail = indexed_details.get(target["url"]) or {}
                actual = len(detail.get("tracks") or [])
                if actual != target["track_total"]:
                    validation_errors.append({
                        "url": target["url"],
                        "message": f"Indexed {actual} of {target['track_total']} songs",
                    })
            if not validation_errors:
                completed += 1
                report("Validating local index")

        final_errors.extend(validation_errors)
        total = release_total + sum(target["track_total"] for target in targets) + artist_phase_total + 1
        complete = release_completed == release_total and completed == total and not final_errors
        target_signature = sorted(
            [[target["url"], target["track_total"]] for target in targets],
            key=lambda item: item[0],
        )
        result = {
            "completed": completed,
            "total": total,
            "percent": 100 if complete else (min(99, int((completed / total) * 100)) if total else 0),
            "release_completed": release_completed,
            "release_total": release_total,
            "errors": final_errors,
            "indexed_at": time.time(),
            "complete": complete,
            "target_urls": target_urls,
            "target_signature": target_signature,
        }
        result["revision"] = self._stable_revision({
            "complete": complete,
            "target_signature": target_signature,
            "details": {
                url: indexed_details[url]
                for url in sorted(indexed_details)
            },
        })
        self._cache.set("full-index-state-v4", result)
        return result

    def _get_target_track_count(self, library_url: str) -> int:
        cached = self._cache.get(f"detail-v2:{library_url}")
        if isinstance(cached, dict):
            return len(cached.get("tracks") or [])
        playlist_id = None
        if library_url == APPLE_LIBRARY_SONGS_URL:
            path = "/songs"
        elif extract_apple_library_album_id(library_url):
            path = f"/albums/{extract_apple_library_album_id(library_url)}/tracks"
        else:
            playlist_id = extract_apple_library_playlist_id(library_url)
            if not playlist_id:
                return 0
            items, total = self._fetch_playlist_items(playlist_id)
            self._cache.set(f"tracks:playlist:{playlist_id}", items)
            return total
        payload = self._get_json(path, {"limit": 1})
        total = (payload.get("meta") or {}).get("total")
        return int(total) if isinstance(total, int) else len(payload.get("data") or [])

    def _build_artist_index(self, source_details: Optional[list[dict]] = None, progress_callback=None) -> dict:
        artists: dict[str, dict] = {}
        seen: dict[str, set] = {}
        processed = 0
        details = source_details if source_details is not None else self._cache.values_with_prefix("detail-v2:")
        for detail in details:
            for track in detail.get("tracks") or []:
                parts = [part.strip() for part in re.split(r"[,;&]", str(track.get("artist") or "")) if part.strip()]
                for artist_name in parts:
                    key = artist_name.casefold()
                    artist = artists.setdefault(key, {
                        "name": artist_name,
                        "image_url": track.get("artwork_url") or "",
                        "content_type": "artist",
                        "track_count": 0,
                        "tracks": [],
                    })
                    identity = (str(track.get("title") or "").casefold(), str(track.get("album") or "").casefold())
                    if identity in seen.setdefault(key, set()):
                        continue
                    seen[key].add(identity)
                    artist["tracks"].append(track)
                processed += 1
                if progress_callback and (processed % 100 == 0):
                    self._notify_index_progress(progress_callback, processed, None)
        for artist in artists.values():
            artist["tracks"].sort(key=lambda item: (
                str(item.get("album") or "").casefold(), int(item.get("position") or 0), str(item.get("title") or "").casefold()
            ))
            artist["track_count"] = len(artist["tracks"])
        summaries = sorted(
            [{"name": item["name"], "image_url": item["image_url"], "track_count": item["track_count"]} for item in artists.values()],
            key=lambda item: item["name"].casefold(),
        )
        artist_total = processed + len(artists)
        if progress_callback:
            self._notify_index_progress(progress_callback, processed, artist_total)
        saved = 0

        def artist_saved():
            nonlocal saved
            saved += 1
            if progress_callback:
                self._notify_index_progress(progress_callback, processed + saved, artist_total)

        detail_rows = {}
        for key, item in artists.items():
            row = {**item, "index_key": key}
            row["revision"] = self._stable_revision(row)
            detail_rows[key] = row
        self._cache.replace_values_with_prefix("artist-detail-v2:", detail_rows, artist_saved)
        result = {
            "artists": summaries,
            "revision": self._stable_revision(summaries),
            "indexed_at": time.time(),
        }
        self._cache.set("artist-index-v2", result)
        return {**result, "details": artists}

    def get_playlist_detail(self, library_url: str, force_refresh: bool = False, index_progress_callback=None) -> dict:
        """Return display-ready metadata without starting a download."""
        cache_key = f"detail-v2:{library_url}"
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached:
                if index_progress_callback:
                    count = len(cached.get("tracks") or [])
                    self._notify_index_progress(index_progress_callback, count, count)
                return self._prepare_detail(cached, from_cache=True)
        if library_url == APPLE_LIBRARY_SONGS_URL:
            name = "Library Songs"
            artwork = None
            tracks = self.get_saved_songs_tracks(force_refresh=force_refresh, index_progress_callback=index_progress_callback)
            content_type = "playlist"
        elif extract_apple_library_album_id(library_url):
            album_id = extract_apple_library_album_id(library_url)
            name, artwork = self._get_library_album_meta(album_id)
            tracks = self.get_library_album_tracks(album_id, force_refresh=force_refresh, index_progress_callback=index_progress_callback)
            content_type = "album"
        else:
            playlist_id = extract_apple_library_playlist_id(library_url)
            if not playlist_id:
                raise ValueError("Invalid Apple Music library playlist URL.")
            name, artwork = self._get_library_playlist_meta(playlist_id)
            tracks = self.get_library_playlist_tracks(playlist_id, force_refresh=force_refresh, index_progress_callback=index_progress_callback)
            content_type = "playlist"

        result = {
            "name": name,
            "url": library_url,
            "image_url": artwork,
            "content_type": content_type,
            "track_count": len(tracks),
            "from_cache": False,
            "tracks": [
                {
                    "title": track.title,
                    "artist": ", ".join(track.artists or []),
                    "album": track.album,
                    "duration_ms": track.duration_ms,
                    "artwork_url": track.artwork_url,
                    "position": track.playlist_position or index,
                }
                for index, track in enumerate(tracks, start=1)
            ],
        }
        result["revision"] = self._stable_revision(result)
        self._cache.set(cache_key, result)
        return result

    def get_artist_detail(self, artist_name: str) -> dict:
        """Read one artist directly from the local v2 detail cache."""
        target = (artist_name or "").strip().casefold()
        if not target:
            raise ValueError("Artist name is required.")
        cache_key = f"artist-detail-v2:{target}"
        detail = self._cache.get(cache_key)
        if isinstance(detail, dict):
            return self._prepare_detail(detail, from_cache=True)

        # Migrate an older bundled artist graph without rebuilding every artist.
        legacy_index = self._cache.get("artist-index-v1") or {}
        legacy_detail = (legacy_index.get("details") or {}).get(target)
        if isinstance(legacy_detail, dict):
            migrated = {**legacy_detail, "index_key": target}
            migrated["revision"] = self._stable_revision(migrated)
            self._cache.set(cache_key, migrated)
            return self._prepare_detail(migrated, from_cache=True)

        artist_index = self._cache.get("artist-index-v2")
        indexed_names = {
            str(item.get("name") or "").strip().casefold()
            for item in ((artist_index or {}).get("artists") or [])
            if isinstance(item, dict)
        }
        if isinstance(artist_index, dict) and target not in indexed_names:
            return self._empty_artist_detail(artist_name)

        # A missing v2 row with a matching summary (or no derived index at all)
        # is a genuine legacy/incomplete-cache case. Rebuild from SQLite release
        # details only; no Apple endpoint is involved.
        rebuilt = self._build_artist_index()
        detail = self._cache.get(cache_key)
        if not isinstance(detail, dict):
            detail = (rebuilt.get("details") or {}).get(target)
        if isinstance(detail, dict):
            return self._prepare_detail(detail, from_cache=True)
        return self._empty_artist_detail(artist_name)

    def _empty_artist_detail(self, artist_name: str) -> dict:
        result = {
            "name": artist_name,
            "content_type": "artist",
            "track_count": 0,
            "from_cache": True,
            "tracks": [],
        }
        result["revision"] = self._stable_revision(result)
        return result

    def _prepare_detail(self, detail: dict, from_cache: bool) -> dict:
        result = {
            **detail,
            "image_url": self._resize_cached_artwork_url(detail.get("image_url"), 600),
            "tracks": [
                {
                    **track,
                    "artwork_url": self._resize_cached_artwork_url(
                        track.get("artwork_url"),
                        160,
                    ),
                }
                for track in (detail.get("tracks") or [])
                if isinstance(track, dict)
            ],
            "from_cache": bool(from_cache),
        }
        result["revision"] = str(detail.get("revision") or self._stable_revision(result))
        return result

    def get_saved_songs_tracks(self, page_callback=None, force_refresh: bool = False, index_progress_callback=None) -> list[TrackMetadata]:
        items = None if force_refresh else self._cache.get("tracks:songs")
        if not isinstance(items, list):
            items = list(self._iter_collection(
                "/songs", params={"limit": 100}, parallel=True,
                item_progress_callback=index_progress_callback,
            ))
            self._cache.set("tracks:songs", items)
        elif index_progress_callback:
            self._notify_index_progress(index_progress_callback, len(items), len(items))
        tracks: list[TrackMetadata] = []
        for item in items:
            meta = self._library_song_to_metadata(item)
            if not meta:
                continue
            meta.playlist_name = "Saved Songs"
            meta.playlist_position = len(tracks) + 1
            meta.request_kind = "playlist"
            tracks.append(meta)
            if page_callback and len(tracks) % 100 == 0:
                try:
                    page_callback(list(tracks))
                except Exception:
                    pass
        if page_callback and tracks:
            try:
                page_callback(list(tracks))
            except Exception:
                pass
        return tracks

    def get_library_playlist_tracks(
        self, playlist_id: str, page_callback=None, force_refresh: bool = False, index_progress_callback=None
    ) -> list[TrackMetadata]:
        playlist_name, playlist_artwork = self._get_library_playlist_meta(playlist_id)
        items = None if force_refresh else self._cache.get(f"tracks:playlist:{playlist_id}")
        if not isinstance(items, list):
            routing = self._cache.get(f"meta:playlist:{playlist_id}")
            if (
                not force_refresh
                and isinstance(routing, dict)
                and routing.get("track_count_known") is True
                and int(routing.get("track_count") or 0) == 0
            ):
                items = []
                if index_progress_callback:
                    self._notify_index_progress(index_progress_callback, 0, 0)
            else:
                items, _ = self._fetch_playlist_items(
                    playlist_id,
                    item_progress_callback=index_progress_callback,
                )
            self._cache.set(f"tracks:playlist:{playlist_id}", items)
        elif index_progress_callback:
            self._notify_index_progress(index_progress_callback, len(items), len(items))
        tracks: list[TrackMetadata] = []
        for item in items:
            meta = self._library_song_to_metadata(item)
            if not meta:
                continue
            meta.playlist_name = playlist_name
            meta.playlist_position = len(tracks) + 1
            meta.playlist_artwork_url = playlist_artwork
            meta.request_kind = "playlist"
            tracks.append(meta)
            if page_callback and len(tracks) % 100 == 0:
                try:
                    page_callback(list(tracks))
                except Exception:
                    pass
        if page_callback and tracks:
            try:
                page_callback(list(tracks))
            except Exception:
                pass
        return tracks

    def get_library_album_tracks(
        self, album_id: str, page_callback=None, force_refresh: bool = False, index_progress_callback=None
    ) -> list[TrackMetadata]:
        album_name, _ = self._get_library_album_meta(album_id)
        items = None if force_refresh else self._cache.get(f"tracks:album:{album_id}")
        if not isinstance(items, list):
            items = list(self._iter_collection(
                f"/albums/{album_id}/tracks", {"limit": 100}, parallel=True,
                item_progress_callback=index_progress_callback,
            ))
            self._cache.set(f"tracks:album:{album_id}", items)
        elif index_progress_callback:
            self._notify_index_progress(index_progress_callback, len(items), len(items))
        tracks: list[TrackMetadata] = []
        for item in items:
            meta = self._library_song_to_metadata(item)
            if not meta:
                continue
            meta.album = meta.album or album_name
            meta.request_kind = "album"
            tracks.append(meta)
        if page_callback and tracks:
            page_callback(list(tracks))
        return tracks

    def _get_saved_songs_count(self) -> int:
        try:
            payload = self._get_json("/songs", params={"limit": 1})
        except Exception as exc:
            logger.warning("[AppleLibrary] saved songs count failed: %s", exc)
            return 0

        meta = payload.get("meta") or {}
        total = meta.get("total")
        if isinstance(total, int):
            return total
        items = payload.get("data") or []
        return len(items)

    def _get_all_playlists(self) -> list[dict]:
        playlists: list[dict] = []
        for item in self._iter_collection(
            "/playlists",
            params={"limit": 100},
            parallel=True,
        ):
            summary = self._playlist_summary(item)
            if summary:
                playlists.append(summary)
        playlists.sort(key=lambda p: (0 if p["is_algorithmic"] else 1, p["name"].lower()))
        return playlists

    def _get_all_albums(self) -> list[dict]:
        albums: list[dict] = []
        for item in self._iter_collection("/albums", params={"limit": 100}, parallel=True):
            attrs = item.get("attributes") or {}
            album_id = str(item.get("id") or "").strip()
            name = (attrs.get("name") or "").strip()
            if not album_id or not name:
                continue
            track_count_value = attrs.get("trackCount")
            artwork = attrs.get("artwork") or {}
            artwork_card = self._artwork_url(artwork, max_px=316)
            artwork_large = self._artwork_url(artwork, max_px=600)
            summary = {
                "id": album_id,
                "name": name,
                "url": f"{APPLE_LIBRARY_ALBUM_URL_PREFIX}{album_id}",
                "image_url": artwork_card,
                "track_count": int(track_count_value or 0),
                "track_count_known": isinstance(track_count_value, int),
                "artist_name": (attrs.get("artistName") or "Unknown Artist").strip(),
                "release_date": (attrs.get("releaseDate") or "")[:10],
            }
            albums.append(summary)
            self._cache.set(f"meta:album:{album_id}", {
                "name": name,
                "artwork": artwork_large,
                "artwork_card": artwork_card,
            })
        albums.sort(key=lambda album: (album["artist_name"].lower(), album["name"].lower()))
        return albums

    def _playlist_summary(self, item: dict) -> Optional[dict]:
        resource_type = str(item.get("type") or "").strip()
        if resource_type and resource_type != "library-playlists":
            logger.debug("[AppleLibrary] Ignoring non-playlist library resource %s", resource_type)
            return None
        attrs = item.get("attributes") or {}
        name = (attrs.get("name") or "").strip()
        if not name:
            return None

        owner_name = (attrs.get("curatorName") or attrs.get("playlistType") or "Apple Music").strip()
        description = self._extract_description(attrs)
        artwork = attrs.get("artwork") or {}
        image_url = self._artwork_url(artwork, max_px=316)
        image_url_large = self._artwork_url(artwork, max_px=600)
        track_count, track_count_known = self._explicit_playlist_track_count(item)

        is_algorithmic = bool(_ALGO_NAMES.search(name))
        owner_lower = owner_name.lower()
        if owner_lower == "apple music" and _ALGO_NAMES.search(description or name):
            is_algorithmic = True

        playlist_id = str(item.get("id") or "").strip()
        if not playlist_id:
            return None

        play_params = attrs.get("playParams") or {}
        global_id = str(play_params.get("globalId") or "").strip()
        tracks_relationship = ((item.get("relationships") or {}).get("tracks") or {})
        tracks_href = str(tracks_relationship.get("href") or "").strip()
        catalog_data = (((item.get("relationships") or {}).get("catalog") or {}).get("data") or [])
        catalog_item = catalog_data[0] if catalog_data else {}
        summary = {
            "id": playlist_id,
            "name": name,
            "url": f"{APPLE_LIBRARY_PLAYLIST_URL_PREFIX}{playlist_id}",
            "image_url": image_url,
            "track_count": track_count,
            "track_count_known": track_count_known,
            "owner_name": owner_name,
            "is_algorithmic": is_algorithmic,
            "description": description,
            "global_id": global_id,
        }
        self._cache.set(f"meta:playlist:{playlist_id}", {
            "name": name,
            "artwork": image_url_large,
            "artwork_card": image_url,
            "global_id": global_id,
            "tracks_href": tracks_href,
            "catalog_id": str(catalog_item.get("id") or "").strip(),
            "catalog_href": str(catalog_item.get("href") or "").strip(),
            "track_count": track_count,
            "track_count_known": track_count_known,
            "routing_complete": bool(catalog_data),
        })
        return summary

    @staticmethod
    def _explicit_playlist_track_count(item: dict) -> tuple[int, bool]:
        attrs = item.get("attributes") or {}
        attribute_count = attrs.get("trackCount")
        if isinstance(attribute_count, int):
            return max(0, attribute_count), True
        relationship = ((item.get("relationships") or {}).get("tracks") or {})
        relationship_count = (relationship.get("meta") or {}).get("total")
        if isinstance(relationship_count, int):
            return max(0, relationship_count), True
        return 0, False

    def _playlist_routing_from_item(self, playlist_id: str, item: dict, base: Optional[dict] = None) -> dict:
        attrs = item.get("attributes") or {}
        relationships = item.get("relationships") or {}
        tracks_relationship = relationships.get("tracks") or {}
        catalog_data = ((relationships.get("catalog") or {}).get("data") or [])
        catalog_item = catalog_data[0] if catalog_data else {}
        track_count, track_count_known = self._explicit_playlist_track_count(item)
        routing = {
            **(base or {}),
            "name": (attrs.get("name") or (base or {}).get("name") or "Apple Music Playlist").strip(),
            "artwork": self._artwork_url(attrs.get("artwork") or {}, max_px=600) or (base or {}).get("artwork"),
            "global_id": str((attrs.get("playParams") or {}).get("globalId") or "").strip(),
            "tracks_href": str(tracks_relationship.get("href") or "").strip(),
            "catalog_id": str(catalog_item.get("id") or "").strip(),
            "catalog_href": str(catalog_item.get("href") or "").strip(),
            "track_count": track_count,
            "track_count_known": track_count_known,
            "routing_complete": True,
        }
        self._cache.set(f"meta:playlist:{playlist_id}", routing)
        return routing

    def _get_playlist_routing(self, playlist_id: str, force_refresh: bool = False) -> dict:
        cache_key = f"meta:playlist:{playlist_id}"
        cached = self._cache.get(cache_key)
        if not force_refresh and isinstance(cached, dict) and cached.get("routing_complete") is True:
            return cached
        base = cached if isinstance(cached, dict) else {}
        payload = self._get_json(
            f"/playlists/{quote(playlist_id, safe='')}",
            {"include": "tracks,catalog", "limit[tracks]": 100},
        )
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"Apple Music returned no resource for library playlist {playlist_id}.")
        return self._playlist_routing_from_item(playlist_id, data[0], base)

    def _collect_playlist_relationship(
        self,
        item: dict,
        item_progress_callback=None,
    ) -> tuple[list[dict], int, bool]:
        relationship = ((item.get("relationships") or {}).get("tracks") or {})
        relationship_included = "data" in relationship or bool(relationship.get("next"))
        track_count, track_count_known = self._explicit_playlist_track_count(item)
        if not relationship_included:
            return [], track_count, False

        items = list(relationship.get("data") or [])
        next_ref = relationship.get("next")
        if item_progress_callback:
            self._notify_index_progress(
                item_progress_callback,
                len(items),
                track_count if track_count_known else None,
            )
        pages = 0
        while next_ref and pages < 200:
            payload = self._get_json(next_ref)
            page_items = payload.get("data") or []
            items.extend(page_items)
            next_ref = payload.get("next")
            pages += 1
            if item_progress_callback:
                self._notify_index_progress(
                    item_progress_callback,
                    len(items),
                    track_count if track_count_known else None,
                )

        if next_ref:
            raise RuntimeError("Apple Music playlist pagination exceeded the 200-page safety limit.")
        if track_count_known and len(items) != track_count:
            raise RuntimeError(
                f"Apple Music reported {track_count} playlist tracks but returned {len(items)}."
            )
        return items, track_count if track_count_known else len(items), True

    def _fetch_playlist_items(
        self,
        playlist_id: str,
        item_progress_callback=None,
    ) -> tuple[list[dict], int]:
        payload = self._get_json(
            f"/playlists/{quote(playlist_id, safe='')}",
            {"include": "tracks,catalog", "limit[tracks]": 100},
        )
        data = payload.get("data") or []
        if not data:
            raise RuntimeError(f"Apple Music returned no resource for library playlist {playlist_id}.")

        library_item = data[0]
        cached = self._cache.get(f"meta:playlist:{playlist_id}")
        routing = self._playlist_routing_from_item(
            playlist_id,
            library_item,
            cached if isinstance(cached, dict) else None,
        )
        items, total, included = self._collect_playlist_relationship(
            library_item,
            item_progress_callback,
        )
        if included:
            return items, total
        if routing.get("track_count_known") is True and int(routing.get("track_count") or 0) == 0:
            if item_progress_callback:
                self._notify_index_progress(item_progress_callback, 0, 0)
            return [], 0

        catalog_path = str(routing.get("catalog_href") or "").strip()
        catalog_id = str(routing.get("catalog_id") or routing.get("global_id") or "").strip()
        if not catalog_path and catalog_id:
            catalog_path = (
                f"/v1/catalog/{quote(self._storefront, safe='')}/playlists/"
                f"{quote(catalog_id, safe='')}"
            )
        if not catalog_path:
            raise RuntimeError(
                f"Apple Music did not include tracks or a catalog resource for {routing.get('name') or playlist_id}."
            )

        catalog_payload = self._get_json(
            catalog_path,
            {"include": "tracks", "limit[tracks]": 100},
        )
        catalog_data = catalog_payload.get("data") or []
        if not catalog_data:
            raise RuntimeError(f"Apple Music returned no catalog resource for playlist {catalog_id or playlist_id}.")
        items, total, included = self._collect_playlist_relationship(
            catalog_data[0],
            item_progress_callback,
        )
        if included:
            return items, total
        catalog_count, catalog_count_known = self._explicit_playlist_track_count(catalog_data[0])
        if catalog_count_known and catalog_count == 0:
            if item_progress_callback:
                self._notify_index_progress(item_progress_callback, 0, 0)
            return [], 0
        raise RuntimeError(
            f"Apple Music reported playlist {catalog_id or playlist_id} without its tracks relationship."
        )

    def _get_library_playlist_meta(self, playlist_id: str) -> tuple[str, Optional[str]]:
        cached = self._cache.get(f"meta:playlist:{playlist_id}")
        if isinstance(cached, dict):
            return cached.get("name") or "Apple Music Playlist", cached.get("artwork")
        try:
            payload = self._get_json(f"/playlists/{playlist_id}")
            data = payload.get("data") or []
            if data:
                attrs = data[0].get("attributes") or {}
                name = (attrs.get("name") or "Apple Music Playlist").strip() or "Apple Music Playlist"
                artwork = self._artwork_url(attrs.get("artwork") or {}, max_px=600)
                self._cache.set(f"meta:playlist:{playlist_id}", {"name": name, "artwork": artwork})
                return name, artwork
        except Exception as exc:
            logger.debug("[AppleLibrary] playlist meta lookup failed for %s: %s", playlist_id, exc)
        return "Apple Music Playlist", None

    def _get_library_album_meta(self, album_id: str) -> tuple[str, Optional[str]]:
        cached = self._cache.get(f"meta:album:{album_id}")
        if isinstance(cached, dict):
            return cached.get("name") or "Apple Music Album", cached.get("artwork")
        try:
            payload = self._get_json(f"/albums/{album_id}")
            data = payload.get("data") or []
            if data:
                attrs = data[0].get("attributes") or {}
                name = (attrs.get("name") or "Apple Music Album").strip() or "Apple Music Album"
                artwork = self._artwork_url(attrs.get("artwork") or {}, max_px=600)
                self._cache.set(f"meta:album:{album_id}", {"name": name, "artwork": artwork})
                return name, artwork
        except Exception as exc:
            logger.debug("[AppleLibrary] album meta lookup failed for %s: %s", album_id, exc)
        return "Apple Music Album", None

    def _iter_collection(self, path: str, params: Optional[dict] = None, parallel: bool = False, item_progress_callback=None):
        progress_count = 0
        progress_total: Optional[int] = None

        def report_items(count: int):
            nonlocal progress_count
            progress_count += count
            if item_progress_callback:
                self._notify_index_progress(item_progress_callback, progress_count, progress_total)

        if parallel and not path.startswith("http"):
            initial_params = dict(params or {})
            limit = min(max(int(initial_params.get("limit") or 100), 1), 100)
            initial_params["limit"] = limit
            first = self._get_json(path, params=initial_params)
            first_items = first.get("data") or []
            total_value = (first.get("meta") or {}).get("total")
            if isinstance(total_value, int):
                progress_total = total_value
            report_items(len(first_items))
            for item in first_items:
                yield item
            total = progress_total
            if isinstance(total, int) and total > len(first_items):
                offsets = list(range(len(first_items), total, limit))[:199]
                pages: dict[int, list] = {}
                with ThreadPoolExecutor(max_workers=min(4, len(offsets))) as pool:
                    futures = {
                        pool.submit(self._get_json, path, {**initial_params, "offset": offset}): offset
                        for offset in offsets
                    }
                    for future in as_completed(futures):
                        page_items = future.result().get("data") or []
                        pages[futures[future]] = page_items
                        report_items(len(page_items))
                for offset in sorted(pages):
                    yield from pages[offset]
                return
            next_ref = first.get("next")
            pages = 1
            while next_ref and pages < 200:
                payload = self._get_json(next_ref)
                page_items = payload.get("data") or []
                report_items(len(page_items))
                yield from page_items
                next_ref = payload.get("next")
                pages += 1
            return

        next_ref: Optional[str] = path
        next_params = dict(params or {})
        pages = 0

        while next_ref and pages < 200:
            payload = self._get_json(next_ref, params=next_params)
            next_params = None
            page_items = payload.get("data") or []
            total_value = (payload.get("meta") or {}).get("total")
            if isinstance(total_value, int):
                progress_total = total_value
            report_items(len(page_items))
            for item in page_items:
                yield item

            next_ref = payload.get("next")
            pages += 1

    @staticmethod
    def _notify_index_progress(callback, completed: int, total: Optional[int]) -> None:
        """Support new (completed, total) and legacy single-argument callbacks."""
        try:
            callback(completed, total)
        except TypeError:
            callback(completed)

    def _get_json(self, path_or_url: str, params: Optional[dict] = None) -> dict:
        if self._cache_only:
            raise RuntimeError(
                "Apple Music network access is unavailable for this cache-only request."
            )
        # Apple pagination links are API-root-relative (for example
        # ``/v1/me/library/songs?offset=100``), while our initial collection
        # paths are library-relative (for example ``/songs``). Treating both
        # forms as library-relative duplicated ``/v1/me/library`` and made the
        # second page fail with a 404.
        if path_or_url.startswith("http"):
            url = path_or_url
        elif path_or_url.startswith("/v1/"):
            url = f"{_APPLE_API_BASE}{path_or_url}"
        else:
            url = f"{_LIBRARY_API_BASE}{path_or_url}"
        resp = self._session.get(url, params=params, timeout=20)
        if resp.status_code == 401:
            raise RuntimeError("Apple Music session expired. Reconnect your account in Settings.")
        if resp.status_code == 403:
            raise RuntimeError("Apple Music library access was denied for this account.")
        resp.raise_for_status()
        return resp.json()

    def _library_song_to_metadata(self, item: dict) -> Optional[TrackMetadata]:
        attrs = item.get("attributes") or {}
        title = (attrs.get("name") or "").strip()
        artist = (attrs.get("artistName") or "").strip()
        if not title or not artist:
            return None

        release_date = (attrs.get("releaseDate") or "")[:10] or None
        release_year = None
        if release_date:
            try:
                release_year = int(release_date[:4])
            except ValueError:
                release_year = None

        play_params = attrs.get("playParams") or {}
        relationships = item.get("relationships") or {}
        catalog_data = ((relationships.get("catalog") or {}).get("data") or [])
        catalog_id = (
            play_params.get("catalogId")
            or play_params.get("id")
            or (catalog_data[0].get("id") if catalog_data else None)
            or item.get("id")
        )

        content_rating = attrs.get("contentRating")
        is_explicit = (
            True if content_rating == "explicit"
            else False if content_rating == "clean"
            else None
        )

        return TrackMetadata(
            title=title,
            artists=self._split_artists(artist),
            album=(attrs.get("albumName") or "").strip() or "Saved Songs",
            duration_ms=attrs.get("durationInMillis"),
            isrc=attrs.get("isrc") or None,
            track_number=attrs.get("trackNumber"),
            disc_number=attrs.get("discNumber"),
            release_date=release_date,
            release_year=release_year,
            artwork_url=self._artwork_url(attrs.get("artwork") or {}, max_px=160),
            genres=attrs.get("genreNames") or [],
            audio_traits=attrs.get("audioTraits") or [],
            is_explicit=is_explicit,
            apple_music_id=str(catalog_id) if catalog_id else None,
        )

    @staticmethod
    def _extract_description(attrs: dict) -> str:
        description = attrs.get("description")
        if isinstance(description, dict):
            return (
                description.get("standard")
                or description.get("short")
                or description.get("editorialNotes")
                or ""
            ).strip()
        return str(description or "").strip()

    @staticmethod
    def _artwork_url(artwork: dict, max_px: int = 600) -> Optional[str]:
        if not isinstance(artwork, dict):
            return None
        template = artwork.get("url") or ""
        if not template:
            return None
        target = max(64, int(max_px or 600))
        source_width = artwork.get("width")
        source_height = artwork.get("height")
        width = str(min(target, source_width) if isinstance(source_width, int) and source_width > 0 else target)
        height = str(min(target, source_height) if isinstance(source_height, int) and source_height > 0 else target)
        return template.replace("{w}", width).replace("{h}", height)

    @staticmethod
    def _split_artists(artist_name: str) -> list[str]:
        if not artist_name:
            return ["Unknown Artist"]
        parts = re.split(r"\s*[,&]\s*|\s+(?:and|feat\.?|ft\.?)\s+", artist_name, flags=re.IGNORECASE)
        artists = [part.strip() for part in parts if part.strip()]
        return artists or [artist_name]
