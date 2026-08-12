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
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
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
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS apple_cache ("
                    "cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
                )
                db.commit()

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=5)

    def get(self, key: str):
        if not self.path:
            return None
        try:
            with closing(self._connect()) as db:
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
            with closing(self._connect()) as db:
                db.execute(
                    "INSERT INTO apple_cache(cache_key, payload, updated_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (f"{self.namespace}:{key}", json.dumps(payload, ensure_ascii=False), now),
                )
                db.commit()
        except Exception as exc:
            logger.debug("[AppleLibrary] cache write failed: %s", exc)

    def values_with_prefix(self, prefix: str) -> list:
        if not self.path:
            return []
        try:
            with closing(self._connect()) as db:
                rows = db.execute(
                    "SELECT payload FROM apple_cache WHERE cache_key LIKE ?",
                    (f"{self.namespace}:{prefix}%",),
                ).fetchall()
            return [json.loads(row[0]) for row in rows]
        except Exception as exc:
            logger.debug("[AppleLibrary] cache prefix read failed: %s", exc)
            return []


class AppleLibraryClient:
    """Fetch a user's Apple Music library (saved songs + library playlists)."""

    def __init__(
        self,
        authorization_token: str,
        music_user_token: str,
        storefront: str = "gb",
        cache_path: Optional[str] = None,
    ):
        auth = (authorization_token or "").strip()
        mut = (music_user_token or "").strip()
        if not auth:
            raise ValueError("Apple Music authorization token is required.")
        if not mut:
            raise ValueError("Apple Music user token is required.")

        self._auth = auth
        self._mut = mut
        self._storefront = (storefront or "gb").strip().lower() or "gb"
        namespace = hashlib.sha256(mut.encode("utf-8")).hexdigest()[:16]
        self._cache = _AppleLibraryCache(cache_path, namespace)
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": self._auth,
            "Music-User-Token": self._mut,
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "User-Agent": _UA,
        })

    def get_library(self, force_refresh: bool = False) -> dict:
        if not force_refresh:
            cached = self._cache.get("library-index-v2")
            if cached:
                cached["from_cache"] = True
                return cached
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
        self._cache.set("library-index-v2", result)
        return result

    def index_entire_library(self, progress_callback=None, force_refresh_summary: bool = True) -> dict:
        """Checkpoint every release and its tracks into the local SQLite index."""
        library = self.get_library(force_refresh=force_refresh_summary)
        targets = []
        targets.extend(
            (item["url"], item["name"], max(1, int(item.get("track_count") or 0)))
            for item in library.get("albums", []) if item.get("url")
        )
        targets.extend(
            (item["url"], item["name"], max(1, int(item.get("track_count") or 0)))
            for item in library.get("playlists", []) if item.get("url")
        )
        release_total = len(targets)
        release_completed = 0
        # A release checkpoint and each of its tracks are separate units of
        # real work. Large in-flight playlists can therefore advance progress.
        total = release_total + sum(weight for _, _, weight in targets)
        completed = 0
        track_progress = {url: 0 for url, _, _ in targets}
        progress_lock = threading.Lock()
        target_urls = sorted(url for url, _, _ in targets)
        # Lists survive the JSON/SQLite cache round-trip unchanged. Tuples would
        # come back as lists and force an unnecessary full re-index every launch.
        target_signature = sorted(
            [[url, weight] for url, _, weight in targets],
            key=lambda item: item[0],
        )
        previous_state = self._cache.get("full-index-state-v2")
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
                    # 100 is reserved for the explicit complete event. This
                    # prevents rounding the final in-flight release to 100.
                    "percent": min(99.9, round((completed / total) * 100, 1)) if total else 99.9,
                    "release_completed": release_completed,
                    "release_total": release_total,
                    "label": label,
                })

        def report_track_progress(url: str, label: str, weight: int, count: int):
            nonlocal completed
            with progress_lock:
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
                        url,
                        False,
                        lambda count, target_url=url, target_label=label, target_weight=weight:
                            report_track_progress(target_url, target_label, target_weight, count),
                    ): (url, label, weight)
                    for url, label, weight in pending
                }
                for future in as_completed(futures):
                    url, label, weight = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        with progress_lock:
                            completed -= track_progress.get(url, 0)
                            track_progress[url] = 0
                            report(f"Retrying {label}")
                        failed.append((url, label, weight, str(exc)))
                    else:
                        with progress_lock:
                            remaining_tracks = weight - track_progress.get(url, 0)
                            track_progress[url] = weight
                            release_completed += 1
                            completed += remaining_tracks + 1
                            report(label)
            pending = [(url, label, weight) for url, label, weight, _ in failed]
            final_errors = [{"url": url, "message": message} for url, _, _, message in failed]
            if pending and attempt < 2:
                time.sleep(0.75 * (attempt + 1))

        complete = release_completed == release_total and completed == total and not final_errors
        result = {
            "completed": completed,
            "total": total,
            "percent": 100 if complete else (min(99.9, round((completed / total) * 100, 1)) if total else 0),
            "release_completed": release_completed,
            "release_total": release_total,
            "errors": final_errors,
            "indexed_at": time.time(),
            "complete": complete,
            "target_urls": target_urls,
            "target_signature": target_signature,
        }
        self._cache.set("full-index-state-v2", result)
        return result

    def get_playlist_detail(self, library_url: str, force_refresh: bool = False, index_progress_callback=None) -> dict:
        """Return display-ready metadata without starting a download."""
        cache_key = f"detail-v2:{library_url}"
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached:
                if index_progress_callback:
                    index_progress_callback(len(cached.get("tracks") or []))
                cached["from_cache"] = True
                return cached
        if library_url == APPLE_LIBRARY_SONGS_URL:
            name = "Favorite Songs"
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
        self._cache.set(cache_key, result)
        return result

    def get_artist_detail(self, artist_name: str) -> dict:
        """Build an instant artist-song view entirely from indexed releases."""
        target = (artist_name or "").strip().casefold()
        if not target:
            raise ValueError("Artist name is required.")
        tracks = []
        seen = set()
        for detail in self._cache.values_with_prefix("detail-v2:"):
            for track in detail.get("tracks") or []:
                artist = str(track.get("artist") or "")
                if target not in [part.strip().casefold() for part in re.split(r"[,;&]", artist) if part.strip()]:
                    continue
                key = (str(track.get("title") or "").casefold(), str(track.get("album") or "").casefold())
                if key in seen:
                    continue
                seen.add(key)
                tracks.append(track)
        tracks.sort(key=lambda item: (str(item.get("album") or "").casefold(), int(item.get("position") or 0), str(item.get("title") or "").casefold()))
        return {"name": artist_name, "content_type": "artist", "track_count": len(tracks), "from_cache": True, "tracks": tracks}

    def get_saved_songs_tracks(self, page_callback=None, force_refresh: bool = False, index_progress_callback=None) -> list[TrackMetadata]:
        items = None if force_refresh else self._cache.get("tracks:songs")
        if not isinstance(items, list):
            items = list(self._iter_collection(
                "/songs", params={"limit": 100}, parallel=True,
                item_progress_callback=index_progress_callback,
            ))
            self._cache.set("tracks:songs", items)
        elif index_progress_callback:
            index_progress_callback(len(items))
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
            path = f"/playlists/{playlist_id}/tracks"
            items = list(self._iter_collection(
                path, params={"limit": 100}, parallel=True,
                item_progress_callback=index_progress_callback,
            ))
            self._cache.set(f"tracks:playlist:{playlist_id}", items)
        elif index_progress_callback:
            index_progress_callback(len(items))
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
            index_progress_callback(len(items))
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
        for item in self._iter_collection("/playlists", params={"limit": 100}):
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
            summary = {
                "id": album_id,
                "name": name,
                "url": f"{APPLE_LIBRARY_ALBUM_URL_PREFIX}{album_id}",
                "image_url": self._artwork_url(attrs.get("artwork") or {}),
                "track_count": int(attrs.get("trackCount") or 0),
                "artist_name": (attrs.get("artistName") or "Unknown Artist").strip(),
                "release_date": (attrs.get("releaseDate") or "")[:10],
            }
            albums.append(summary)
            self._cache.set(f"meta:album:{album_id}", {"name": name, "artwork": summary["image_url"]})
        albums.sort(key=lambda album: (album["artist_name"].lower(), album["name"].lower()))
        return albums

    def _playlist_summary(self, item: dict) -> Optional[dict]:
        attrs = item.get("attributes") or {}
        name = (attrs.get("name") or "").strip()
        if not name:
            return None

        owner_name = (attrs.get("curatorName") or attrs.get("playlistType") or "Apple Music").strip()
        description = self._extract_description(attrs)
        image_url = self._artwork_url(attrs.get("artwork") or {})
        track_count = (
            attrs.get("trackCount")
            or ((item.get("relationships") or {}).get("tracks") or {}).get("meta", {}).get("total")
            or 0
        )

        is_algorithmic = bool(_ALGO_NAMES.search(name))
        owner_lower = owner_name.lower()
        if owner_lower == "apple music" and _ALGO_NAMES.search(description or name):
            is_algorithmic = True

        playlist_id = str(item.get("id") or "").strip()
        if not playlist_id:
            return None

        summary = {
            "id": playlist_id,
            "name": name,
            "url": f"{APPLE_LIBRARY_PLAYLIST_URL_PREFIX}{playlist_id}",
            "image_url": image_url,
            "track_count": int(track_count or 0),
            "owner_name": owner_name,
            "is_algorithmic": is_algorithmic,
            "description": description,
        }
        self._cache.set(f"meta:playlist:{playlist_id}", {"name": name, "artwork": image_url})
        return summary

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
                artwork = self._artwork_url(attrs.get("artwork") or {})
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
                artwork = self._artwork_url(attrs.get("artwork") or {})
                self._cache.set(f"meta:album:{album_id}", {"name": name, "artwork": artwork})
                return name, artwork
        except Exception as exc:
            logger.debug("[AppleLibrary] album meta lookup failed for %s: %s", album_id, exc)
        return "Apple Music Album", None

    def _iter_collection(self, path: str, params: Optional[dict] = None, parallel: bool = False, item_progress_callback=None):
        progress_count = 0

        def report_items(count: int):
            nonlocal progress_count
            progress_count += count
            if item_progress_callback:
                item_progress_callback(progress_count)

        if parallel and not path.startswith("http"):
            initial_params = dict(params or {})
            limit = min(max(int(initial_params.get("limit") or 100), 1), 100)
            initial_params["limit"] = limit
            first = self._get_json(path, params=initial_params)
            first_items = first.get("data") or []
            report_items(len(first_items))
            for item in first_items:
                yield item
            total = (first.get("meta") or {}).get("total")
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
            report_items(len(page_items))
            for item in page_items:
                yield item

            next_ref = payload.get("next")
            pages += 1

    def _get_json(self, path_or_url: str, params: Optional[dict] = None) -> dict:
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
            artwork_url=self._artwork_url(attrs.get("artwork") or {}),
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
    def _artwork_url(artwork: dict) -> Optional[str]:
        if not isinstance(artwork, dict):
            return None
        template = artwork.get("url") or ""
        if not template:
            return None
        width = str(artwork.get("width") or 1200)
        height = str(artwork.get("height") or 1200)
        return template.replace("{w}", width).replace("{h}", height)

    @staticmethod
    def _split_artists(artist_name: str) -> list[str]:
        if not artist_name:
            return ["Unknown Artist"]
        parts = re.split(r"\s*[,&]\s*|\s+(?:and|feat\.?|ft\.?)\s+", artist_name, flags=re.IGNORECASE)
        artists = [part.strip() for part in parts if part.strip()]
        return artists or [artist_name]
