"""
Link resolver — cross-platform track ID lookup.

Authoritative Odesli results are supplemented by bounded parallel fallbacks.
Resolved IDs and short-lived misses are persisted in a local SQLite WAL cache;
the former JSON cache is imported once and remains readable during migration.

Returns a dict mapping platform name → ID/ASIN string, e.g.:
    {"amazonMusic": "B07XVMPVHD", "tidal": "12345678", "qobuz": "abcdef"}

Never raises — returns {} on total failure.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from functools import wraps
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Callable, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

_ODESLI_API = "https://api.song.link/v1-alpha.1/links"
_SONGWHIP_API = "https://api.songwhip.com/v3/resolve"
_AMAZON_SEARCH = "https://www.amazon.com/s"
_AMAZON_SEARCH_ENDPOINTS = (
    "https://www.amazon.com/s",
    "https://www.amazon.in/s",
    "https://www.amazon.co.uk/s",
    "https://www.amazon.com.br/s",
)
# Kept as the legacy name for compatibility with existing callers/tests.
_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".antra_link_cache.json")
_CACHE_DB_FILE = os.path.join(os.path.expanduser("~"), ".antra_link_cache.sqlite3")
_ODESLI_RETRY_DELAYS = [2, 5]
_NEGATIVE_CACHE_TTL_SECONDS = 120.0
_FALLBACK_WORKERS = 3
_RESOLUTION_LOCK_STRIPES = 64
_SQLITE_BUSY_TIMEOUT_MS = 3000
_SQLITE_LOCK_RETRY_DELAYS = (0.01, 0.025, 0.05, 0.1, 0.2)

_REQUIRED_PLATFORM_ALIASES = {
    "amazon": "amazonMusic",
    "amazonmusic": "amazonMusic",
    "apple": "appleMusic",
    "applemusic": "appleMusic",
    "itunes": "appleMusic",
    "tidal": "tidal",
    "tidal_mirror": "tidal",
    "hifi": "tidal",
}
_RESULT_PLATFORM_KEYS = {
    "amazonMusic": ("amazonMusic", "amazon"),
    "appleMusic": ("appleMusic", "itunes"),
    "tidal": ("tidal",),
}
_ALL_DOWNSTREAM_PLATFORMS = frozenset(_RESULT_PLATFORM_KEYS)
_CACHE_PLATFORM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_CREDENTIAL_FIELD_FRAGMENTS = (
    "auth",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)
_LEGACY_SPOTIFY_KEY_RE = re.compile(r"^[A-Za-z0-9]{22}$")
_LEGACY_ISRC_KEY_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")
_DB_ERROR = object()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/html, */*",
}

_SONGSTATS_SCRIPT_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def _sanitize_cache_result(value) -> dict[str, str]:
    """Keep only public platform IDs; request parameters and credentials never enter the cache."""
    if not isinstance(value, dict):
        return {}
    clean: dict[str, str] = {}
    for platform, raw_id in value.items():
        platform_name = str(platform or "").strip()
        lowered_platform = platform_name.lower()
        if (
            not _CACHE_PLATFORM_RE.fullmatch(platform_name)
            or any(
                fragment in lowered_platform
                for fragment in _CREDENTIAL_FIELD_FRAGMENTS
            )
            or not isinstance(raw_id, (str, int))
        ):
            continue
        platform_id = str(raw_id).strip()
        if platform_id and len(platform_id) <= 512 and not any(
            ord(char) < 32 for char in platform_id
        ):
            clean[platform_name] = platform_id
    return clean


def _legacy_cache_key_is_safe(value: object) -> bool:
    key = str(value or "").strip()
    return bool(
        _LEGACY_SPOTIFY_KEY_RE.fullmatch(key)
        or _LEGACY_ISRC_KEY_RE.fullmatch(key)
    )


def _load_cache(path: Optional[str] = None) -> dict[str, dict[str, str]]:
    """Read valid entries from the former JSON cache without ever writing it."""
    cache_path = path or _CACHE_FILE
    try:
        if not os.path.exists(cache_path):
            return {}
        with open(cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_key, raw_value in payload.items():
        if not _legacy_cache_key_is_safe(raw_key):
            continue
        clean = _sanitize_cache_result(raw_value)
        if clean:
            result[str(raw_key)] = clean
    return result


class _SQLiteLinkCache:
    """Small process-safe cache using atomic rows and SQLite-managed WAL batching."""

    def __init__(
        self,
        db_path: str,
        legacy_path: str,
        *,
        negative_ttl: float,
        clock: Callable[[], float],
    ):
        self._db_path = db_path
        self._legacy_path = legacy_path
        self._negative_ttl = max(0.0, float(negative_ttl))
        self._clock = clock
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None
        self._connection_pid: Optional[int] = None
        self._volatile: dict[
            str,
            tuple[dict[str, str], dict[str, float]],
        ] = {}
        self._legacy_entries: dict[str, dict[str, str]] = {}

        # Schema setup and migration are best effort: cache failure must never stop
        # a download, and later operations retry transient SQLite lock contention.
        self._run(lambda connection: True, default=False)
        self._migrate_legacy_once()

    def _connect_locked(self) -> sqlite3.Connection:
        pid = os.getpid()
        if self._connection is not None and self._connection_pid == pid:
            return self._connection
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass

        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        connection = sqlite3.connect(
            self._db_path,
            timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000.0,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS link_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    is_negative INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS link_cache_misses (
                    cache_key TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (cache_key, platform)
                )
                """
            )
        except Exception:
            connection.close()
            raise

        self._connection = connection
        self._connection_pid = pid
        try:
            if os.name != "nt":
                os.chmod(self._db_path, 0o600)
        except OSError:
            pass
        return connection

    @staticmethod
    def _is_lock_error(error: sqlite3.OperationalError) -> bool:
        message = str(error).lower()
        return "locked" in message or "busy" in message

    def _run(self, operation, *, default):
        delays = (0.0,) + _SQLITE_LOCK_RETRY_DELAYS
        for index, delay in enumerate(delays):
            if delay:
                time.sleep(delay)
            try:
                with self._lock:
                    return operation(self._connect_locked())
            except sqlite3.OperationalError as error:
                if self._is_lock_error(error) and index + 1 < len(delays):
                    continue
                logger.debug("[LinkResolver] SQLite cache unavailable: %s", error)
                return default
            except (OSError, sqlite3.DatabaseError) as error:
                logger.debug("[LinkResolver] SQLite cache unavailable: %s", error)
                return default
        return default

    def _migrate_legacy_once(self) -> None:
        marker = self._run(
            lambda connection: connection.execute(
                "SELECT value FROM cache_metadata WHERE name = ?",
                ("legacy_json_v1_migrated",),
            ).fetchone(),
            default=_DB_ERROR,
        )
        if marker is not _DB_ERROR and marker:
            return

        entries = _load_cache(self._legacy_path)
        self._legacy_entries = dict(entries)
        timestamp = float(self._clock())

        def migrate(connection: sqlite3.Connection) -> bool:
            connection.execute("BEGIN IMMEDIATE")
            try:
                marker = connection.execute(
                    "SELECT value FROM cache_metadata WHERE name = ?",
                    ("legacy_json_v1_migrated",),
                ).fetchone()
                if marker:
                    connection.rollback()
                    return True
                if entries:
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO link_cache
                            (cache_key, result_json, is_negative, expires_at, updated_at)
                        VALUES (?, ?, 0, NULL, ?)
                        """,
                        [
                            (
                                key,
                                json.dumps(value, sort_keys=True, separators=(",", ":")),
                                timestamp,
                            )
                            for key, value in entries.items()
                        ],
                    )
                connection.execute(
                    "INSERT INTO cache_metadata(name, value) VALUES (?, ?)",
                    ("legacy_json_v1_migrated", str(int(timestamp))),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

        if self._run(migrate, default=False):
            self._legacy_entries.clear()

    def get(
        self,
        cache_key: str,
    ) -> tuple[bool, dict[str, str], frozenset[str]]:
        now = float(self._clock())
        with self._lock:
            volatile = self._volatile.get(cache_key)
            if volatile is not None:
                value, misses = volatile
                active_misses = {
                    platform
                    for platform, expires_at in misses.items()
                    if expires_at > now
                }
                if value or active_misses:
                    return True, dict(value), frozenset(active_misses)
                self._volatile.pop(cache_key, None)

        cached = self._run(
            lambda connection: (
                connection.execute(
                    """
                    SELECT result_json, is_negative, expires_at
                    FROM link_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone(),
                connection.execute(
                    """
                    SELECT platform, expires_at
                    FROM link_cache_misses
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchall(),
            ),
            default=_DB_ERROR,
        )
        if cached is _DB_ERROR:
            legacy = self._legacy_entries.get(cache_key)
            return (
                (True, dict(legacy), frozenset())
                if legacy
                else (False, {}, frozenset())
            )
        row, miss_rows = cached
        if row is None:
            legacy = self._legacy_entries.get(cache_key)
            return (
                (True, dict(legacy), frozenset())
                if legacy
                else (False, {}, frozenset())
            )

        raw_json, is_negative, expires_at = row
        try:
            value = _sanitize_cache_result(json.loads(raw_json))
        except Exception:
            value = {}
        active_misses = frozenset(
            str(platform)
            for platform, miss_expires_at in miss_rows
            if float(miss_expires_at) > now
        )
        expired_misses = [
            str(platform)
            for platform, miss_expires_at in miss_rows
            if float(miss_expires_at) <= now
        ]
        if expired_misses:
            def delete_expired_misses(connection: sqlite3.Connection) -> None:
                connection.executemany(
                    """
                    DELETE FROM link_cache_misses
                    WHERE cache_key = ? AND platform = ?
                    """,
                    [(cache_key, platform) for platform in expired_misses],
                )
                connection.commit()

            self._run(
                delete_expired_misses,
                default=None,
            )
        if is_negative and expires_at is not None and float(expires_at) <= now:
            if not active_misses:
                return False, {}, frozenset()
        if value or active_misses:
            return True, value, active_misses
        return False, {}, frozenset()

    def put(
        self,
        cache_key: str,
        result: dict[str, str],
        missing_platforms: Iterable[str] = (),
    ) -> None:
        clean = _sanitize_cache_result(result)
        missing = self._normalize_missing_platforms(missing_platforms, clean)
        is_negative = not clean
        now = float(self._clock())
        expires_at = now + self._negative_ttl if is_negative else None
        miss_expires_at = now + self._negative_ttl
        payload = json.dumps(clean, sort_keys=True, separators=(",", ":"))

        def store(connection: sqlite3.Connection) -> bool:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO link_cache
                        (cache_key, result_json, is_negative, expires_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        result_json = excluded.result_json,
                        is_negative = excluded.is_negative,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at
                    """,
                    (cache_key, payload, int(is_negative), expires_at, now),
                )
                present = {
                    platform
                    for platform in _ALL_DOWNSTREAM_PLATFORMS
                    if any(clean.get(key) for key in _RESULT_PLATFORM_KEYS[platform])
                }
                if present:
                    connection.executemany(
                        """
                        DELETE FROM link_cache_misses
                        WHERE cache_key = ? AND platform = ?
                        """,
                        [(cache_key, platform) for platform in present],
                    )
                if missing:
                    connection.executemany(
                        """
                        INSERT INTO link_cache_misses(cache_key, platform, expires_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(cache_key, platform) DO UPDATE SET
                            expires_at = excluded.expires_at
                        """,
                        [
                            (cache_key, platform, miss_expires_at)
                            for platform in sorted(missing)
                        ],
                    )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

        stored = self._run(
            store,
            default=False,
        )
        with self._lock:
            if stored:
                self._volatile.pop(cache_key, None)
            else:
                self._volatile[cache_key] = (
                    clean,
                    {
                        platform: miss_expires_at
                        for platform in missing
                    },
                )

    @staticmethod
    def _normalize_missing_platforms(
        missing_platforms: Iterable[str],
        result: dict[str, str],
    ) -> frozenset[str]:
        normalized = {
            platform
            for platform in missing_platforms
            if platform in _ALL_DOWNSTREAM_PLATFORMS
        }
        return frozenset(
            platform
            for platform in normalized
            if not any(result.get(key) for key in _RESULT_PLATFORM_KEYS[platform])
        )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None
                self._connection_pid = None


def _to_slug(s: str) -> str:
    """Convert a string to a Songwhip-style URL slug."""
    s = s.lower()
    s = re.sub(r"[&/\\|]", " ", s)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s)
    return s


def _extract_amazon_asin(value: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None

    for pattern in (
        r"trackAsin=([A-Z0-9]{10})",
        r"/albums/[A-Z0-9]{10}/([A-Z0-9]{10})",
        r"/tracks/([A-Z0-9]{10})",
        r"\b(B[0-9A-Z]{9})\b",
    ):
        match = re.search(pattern, raw)
        if match:
            return match.group(1)

    if re.fullmatch(r"[A-Z0-9]{10}", raw):
        return raw
    return None


def _singleflight_link_resolution(method):
    """Serialize equal track lookups so concurrent adapters reuse the first result."""
    @wraps(method)
    def wrapped(self, track, required_platforms=None):
        cache_key = self._cache_key_for_track(track)
        lock = self._resolution_locks[hash(cache_key) % len(self._resolution_locks)]
        with lock:
            return method(self, track, required_platforms)

    return wrapped


class OdesliEnricher:
    """
    Resolve platform-specific track IDs with deterministic source precedence.

    One immutable requests Session and one small executor are reused for the
    lifetime of the enricher. The session's urllib3 pools and cookie jar support
    concurrent requests; close is coordinated so shared state is never mutated
    while a request is being scheduled.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        required_platforms: Optional[Iterable[str]] = None,
        cache_db_path: Optional[str] = None,
        legacy_cache_path: Optional[str] = None,
        negative_ttl: float = _NEGATIVE_CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
        fallback_workers: int = _FALLBACK_WORKERS,
        session: Optional[requests.Session] = None,
    ):
        self._api_key = api_key
        self._configured_required_platforms = (
            self._normalize_required_platforms(required_platforms)
            if required_platforms is not None
            else None
        )
        self._cache = _SQLiteLinkCache(
            cache_db_path or _CACHE_DB_FILE,
            legacy_cache_path or _CACHE_FILE,
            negative_ttl=negative_ttl,
            clock=clock,
        )
        self._session = session or requests.Session()
        self._session.headers.update(_HEADERS)
        worker_count = max(1, min(_FALLBACK_WORKERS, int(fallback_workers)))
        self._request_slots = threading.BoundedSemaphore(worker_count)
        self._resolution_locks = [
            threading.Lock()
            for _ in range(_RESOLUTION_LOCK_STRIPES)
        ]
        self._session_state_lock = threading.RLock()
        self._session_idle = threading.Condition(self._session_state_lock)
        self._active_requests = 0
        self._fallback_context = threading.local()
        self._closed = False
        self._fallback_executor = ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="antra-link-fallback",
        )

    # ── Public interface ──────────────────────────────────────────────────────

    @_singleflight_link_resolution
    def resolve(
        self,
        track,
        required_platforms: Optional[Iterable[str]] = None,
    ) -> dict[str, str]:
        """
        Return {platform: id} for the given track.
        Merge in fixed precedence order, regardless of fallback completion order.

        Resolver order:
          1. Odesli (if Spotify ID or ISRC available) — exact cross-platform match,
             rate-limited but authoritative. Prevents wrong ASINs from text search.
          2. Songwhip, Songstats, and Amazon search start concurrently.
          3. Songwhip and Songstats are merged in that order.
          4. Deezer → song.link, then iTunes → Odesli.
          5. The already-running Amazon result is merged last.

        Lookup stages stop as soon as every ID consumed by an enabled downstream
        adapter is present. Cached partial results are enriched rather than treated
        as permanently complete.
        """
        cache_key = self._cache_key_for_track(track)
        required = self._required_platforms(required_platforms)
        cache_hit, cached, negative_platforms = self._cache.get(cache_key)
        lookup_required = frozenset(required.difference(negative_platforms))

        result: dict[str, str] = dict(cached)
        if cache_hit and self._has_required_ids(result, lookup_required):
            logger.debug(
                "[LinkResolver] Cache hit for '%s' (negative=%s)",
                track.title,
                sorted(negative_platforms),
            )
            return result
        if not lookup_required:
            return result

        # 1. Odesli — exact match via Spotify ID or ISRC (highest accuracy).
        if getattr(track, "spotify_id", None) or getattr(track, "isrc", None):
            self._merge_missing(result, self._try_odesli(track))
            if self._has_required_ids(result, lookup_required):
                return self._finish_resolution(
                    track,
                    cache_key,
                    result,
                    lookup_required,
                )

        stop_event = threading.Event()
        futures: dict[str, Future] = {}
        futures["songwhip"] = self._fallback_executor.submit(
            self._run_fallback,
            stop_event,
            self._try_songwhip,
            track,
        )
        missing = self._missing_required_ids(result, lookup_required)
        if getattr(track, "isrc", None) and missing.intersection(
            {"amazonMusic", "tidal"}
        ):
            futures["songstats"] = self._fallback_executor.submit(
                self._run_fallback,
                stop_event,
                self._try_songstats,
                track,
            )
        if "amazonMusic" in missing:
            futures["amazon"] = self._fallback_executor.submit(
                self._run_fallback,
                stop_event,
                self._search_amazon_ids,
                track,
            )

        self._merge_missing(
            result,
            self._future_result("Songwhip", futures.pop("songwhip")),
        )
        if self._has_required_ids(result, lookup_required):
            self._cancel_futures(futures, stop_event)
            return self._finish_resolution(
                track,
                cache_key,
                result,
                lookup_required,
            )

        songstats_future = futures.pop("songstats", None)
        if songstats_future is not None:
            self._merge_missing(
                result,
                self._future_result("Songstats", songstats_future),
            )
            if self._has_required_ids(result, lookup_required):
                self._cancel_futures(futures, stop_event)
                return self._finish_resolution(
                    track,
                    cache_key,
                    result,
                    lookup_required,
                )

        # Higher-precedence cross-platform fallbacks remain sequential so a fuzzy
        # Amazon result can never mask an exact ID merely because it finished first.
        if getattr(track, "isrc", None):
            self._merge_missing(result, self._try_deezer_songlink(track))
            if self._has_required_ids(result, lookup_required):
                self._cancel_futures(futures, stop_event)
                return self._finish_resolution(
                    track,
                    cache_key,
                    result,
                    lookup_required,
                )

        self._merge_missing(result, self._try_itunes_odesli(track))
        if self._has_required_ids(result, lookup_required):
            self._cancel_futures(futures, stop_event)
            return self._finish_resolution(
                track,
                cache_key,
                result,
                lookup_required,
            )

        amazon_future = futures.pop("amazon", None)
        if amazon_future is not None:
            self._merge_missing(
                result,
                self._future_result("Amazon Search", amazon_future),
            )

        self._cancel_futures(futures, stop_event)
        return self._finish_resolution(
            track,
            cache_key,
            result,
            lookup_required,
        )

    def close(self) -> None:
        with self._session_state_lock:
            if self._closed:
                return
            self._closed = True
        self._fallback_executor.shutdown(wait=True, cancel_futures=True)
        with self._session_idle:
            while self._active_requests:
                self._session_idle.wait()
        self._session.close()
        self._cache.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @staticmethod
    def _normalize_required_platforms(platforms: Iterable[str]) -> frozenset[str]:
        normalized: set[str] = set()
        for platform in platforms:
            token = str(platform or "").strip()
            if not token:
                continue
            canonical = _REQUIRED_PLATFORM_ALIASES.get(token.lower())
            if canonical:
                normalized.add(canonical)
            elif token in _RESULT_PLATFORM_KEYS:
                normalized.add(token)
        return frozenset(normalized)

    @classmethod
    def _platforms_from_source_setting(
        cls,
        value: str,
        *,
        default: frozenset[str],
    ) -> frozenset[str]:
        raw = str(value or "").strip()
        if not raw:
            return default
        tokens = {
            token.strip("[]'\"").lower()
            for token in re.split(r"[,;\s]+", raw)
            if token.strip("[]'\"")
        }
        if not tokens or "auto" in tokens:
            return default
        if "priority-2" in tokens:
            return frozenset({"amazonMusic", "appleMusic"})
        if tokens.intersection({"priority-3", "priority-4"}):
            return frozenset()
        return cls._normalize_required_platforms(tokens)

    def _required_platforms(
        self,
        override: Optional[Iterable[str]],
    ) -> frozenset[str]:
        if override is not None:
            return self._normalize_required_platforms(override)
        if self._configured_required_platforms is not None:
            return self._configured_required_platforms

        enabled = self._platforms_from_source_setting(
            os.getenv("SOURCES_ENABLED", ""),
            default=_ALL_DOWNSTREAM_PLATFORMS,
        )
        preferred = self._platforms_from_source_setting(
            os.getenv("SOURCE_PREFERENCES", ""),
            default=_ALL_DOWNSTREAM_PLATFORMS,
        )
        return frozenset(enabled.intersection(preferred))

    @staticmethod
    def _cache_key_for_track(track) -> str:
        identifier = (
            getattr(track, "spotify_id", None)
            or getattr(track, "isrc", None)
            or ""
        )
        raw_identifier = str(identifier).strip()
        if raw_identifier and _legacy_cache_key_is_safe(raw_identifier):
            return raw_identifier
        if raw_identifier:
            digest = hashlib.sha256(raw_identifier.encode("utf-8")).hexdigest()
            return f"identifier:{digest}"

        identity = {
            "title": str(getattr(track, "title", "") or ""),
            "artists": [
                str(artist)
                for artist in (getattr(track, "artists", None) or [])
            ],
            "album": str(getattr(track, "album", "") or ""),
        }
        digest = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"metadata:{digest}"

    @staticmethod
    def _missing_required_ids(
        result: dict[str, str],
        required: frozenset[str],
    ) -> set[str]:
        return {
            platform
            for platform in required
            if not any(result.get(key) for key in _RESULT_PLATFORM_KEYS[platform])
        }

    @classmethod
    def _has_required_ids(
        cls,
        result: dict[str, str],
        required: frozenset[str],
    ) -> bool:
        return not cls._missing_required_ids(result, required)

    @staticmethod
    def _merge_missing(
        result: dict[str, str],
        candidate: dict[str, str],
    ) -> None:
        for platform, platform_id in candidate.items():
            if platform_id:
                result.setdefault(platform, str(platform_id))

    @staticmethod
    def _cancel_futures(
        futures: dict[str, Future],
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if stop_event is not None:
            stop_event.set()
        for future in futures.values():
            future.cancel()

    @staticmethod
    def _future_result(label: str, future: Future) -> dict[str, str]:
        try:
            value = future.result()
            return value if isinstance(value, dict) else {}
        except Exception as error:
            logger.debug("[%s] Parallel fallback failed: %s", label, error)
            return {}

    def _search_amazon_ids(self, track) -> dict[str, str]:
        asin = self._search_amazon(track)
        return {"amazonMusic": asin} if asin else {}

    def _run_fallback(
        self,
        stop_event: threading.Event,
        callback,
        track,
    ):
        self._fallback_context.stop_event = stop_event
        try:
            if stop_event.is_set():
                return {}
            return callback(track)
        finally:
            self._fallback_context.stop_event = None

    def _finish_resolution(
        self,
        track,
        cache_key: str,
        result: dict[str, str],
        attempted_platforms: frozenset[str],
    ) -> dict[str, str]:
        if result:
            logger.debug(
                "[LinkResolver] Resolved '%s': %s",
                track.title,
                list(result.keys()),
            )
        else:
            logger.debug("[LinkResolver] No platform IDs found for '%s'", track.title)
        self._store(
            cache_key,
            result,
            self._missing_required_ids(result, attempted_platforms),
        )
        return result

    def _get(self, url: str, **kwargs):
        # Session configuration is immutable after __init__. requests delegates
        # transport to thread-safe urllib3 pools; this bound additionally prevents
        # resolver fan-out from creating unbounded simultaneous requests.
        with self._request_slots:
            stop_event = getattr(self._fallback_context, "stop_event", None)
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("Link fallback was cancelled")
            with self._session_idle:
                if self._closed:
                    raise RuntimeError("OdesliEnricher is closed")
                session = self._session
                self._active_requests += 1
            try:
                return session.get(url, **kwargs)
            finally:
                with self._session_idle:
                    self._active_requests -= 1
                    if not self._active_requests:
                        self._session_idle.notify_all()

    # ── Amazon product search ─────────────────────────────────────────────────

    def _search_amazon(self, track) -> Optional[str]:
        """
        Search amazon.com/s for the track in the digital-music-track category.
        Extracts the best-matching ASIN using title similarity scoring.
        No API key, no rate limits.
        """
        title = getattr(track, "title", "") or ""
        artists = getattr(track, "artists", []) or []
        artist = artists[0] if artists else ""
        if not title:
            return None

        # Strip parenthetical collaboration credits so "YouUgly (with Westside Gunn)"
        # searches as "YouUgly JID" — Amazon catalog titles rarely include these.
        title_clean = re.sub(
            r'\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]+[\)\]]',
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        query = f"{title_clean} {artist}".strip()
        try:
            pairs = []
            for endpoint in _AMAZON_SEARCH_ENDPOINTS:
                try:
                    resp = self._get(
                        endpoint,
                        params={"k": query, "i": "digital-music-track"},
                        timeout=10,
                    )
                    if not resp.ok:
                        logger.debug(f"[Amazon Search] {endpoint} HTTP {resp.status_code}")
                        continue
                except Exception as e:
                    logger.debug(f"[Amazon Search] {endpoint} request failed: {e}")
                    continue

                # Extract (ASIN, product title) pairs from the result page.
                # Amazon embeds data-asin on product cards alongside h2 > span title text.
                pairs = re.findall(
                    r'data-asin="([A-Z0-9]{10})"[^>]*>.*?<h2[^>]*>.*?<span[^>]*>([^<]+)</span>',
                    resp.text[:400000],
                    re.DOTALL,
                )
                if not pairs:
                    continue

                title_lower = title.lower()
                title_clean_lower = title_clean.lower()

                for asin, product_title in pairs:
                    pt_lower = product_title.strip().lower()
                    # Accept if product title contains our track title (case-insensitive).
                    # Also match against the collaboration-stripped title so "YouUgly" matches
                    # a product titled "YouUgly (with Westside Gunn)" and vice-versa.
                    if (title_lower in pt_lower or pt_lower in title_lower
                            or title_clean_lower in pt_lower or pt_lower in title_clean_lower):
                        logger.debug(f"[Amazon Search] Matched '{product_title.strip()}' → {asin}")
                        return asin

                # Looser fallback: first result (Amazon ranks by relevance)
                asin, product_title = pairs[0]
                logger.debug(f"[Amazon Search] Using first result '{product_title.strip()}' → {asin}")
                return asin
        except Exception as e:
            logger.debug(f"[Amazon Search] Request failed: {e}")
            return None

        return None

    # ── Songwhip ──────────────────────────────────────────────────────────────

    def _try_itunes_odesli(self, track) -> dict[str, str]:
        """
        Resolve via iTunes Search API → Odesli(Apple Music URL).

        iTunes Search has no auth and no rate limit. We use it to get an Apple Music
        track ID, then feed that URL to Odesli, which is less rate-limited on the
        Apple Music path than on the Spotify path (different quota bucket).

        Useful when: unauthenticated Spotify path has no ISRCs, Odesli 429'd on
        the Spotify URL, and Songwhip has no index entry for the track.
        """
        title = getattr(track, "title", "") or ""
        artists = getattr(track, "artists", []) or []
        artist = artists[0] if artists else ""
        if not title:
            return {}

        # Strip collaboration credits for better iTunes match
        _COLLAB_RE = re.compile(
            r'\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]+[\)\]]',
            re.IGNORECASE,
        )
        title_clean = _COLLAB_RE.sub("", title).strip()

        try:
            r = self._get(
                "https://itunes.apple.com/search",
                params={"term": f"{title_clean} {artist}", "media": "music",
                        "entity": "song", "limit": 3},
                timeout=8,
            )
            if not r.ok:
                return {}
            results = r.json().get("results", [])
        except Exception as e:
            logger.debug(f"[iTunes] Search failed for '{title}': {e}")
            return {}

        apple_track_id: Optional[str] = None
        for item in results:
            track_url = item.get("trackViewUrl", "")
            m = re.search(r"[?&]i=(\d+)", track_url)
            if not m:
                continue
            # Verify title similarity before accepting
            itunes_title = item.get("trackName", "").lower()
            title_clean_lower = title_clean.lower()
            if title_clean_lower in itunes_title or itunes_title in title_clean_lower:
                apple_track_id = m.group(1)
                break
        # Looser fallback: accept first result
        if not apple_track_id and results:
            track_url = results[0].get("trackViewUrl", "")
            m = re.search(r"[?&]i=(\d+)", track_url)
            if m:
                apple_track_id = m.group(1)

        if not apple_track_id:
            logger.debug(f"[iTunes] No track ID found for '{title}'")
            return {}

        apple_url = f"https://music.apple.com/us/album/-/id?i={apple_track_id}"
        logger.debug(f"[iTunes] Found Apple Music track {apple_track_id} for '{title}' — querying Odesli")

        try:
            r2 = self._get(
                _ODESLI_API,
                params={"url": apple_url, "platform": "appleMusic", "type": "song"},
                timeout=8,
            )
            if r2.status_code == 429:
                logger.debug(f"[iTunes→Odesli] Rate limited for '{title}'")
                return {}
            if not r2.ok:
                logger.debug(f"[iTunes→Odesli] HTTP {r2.status_code} for '{title}'")
                return {}
            return self._extract_odesli(r2.json(), title)
        except Exception as e:
            logger.debug(f"[iTunes→Odesli] Request failed for '{title}': {e}")
            return {}

    def _try_songwhip(self, track) -> dict[str, str]:
        """
        Fetch from Songwhip's public slug-based API.
        Only works for tracks already indexed by Songwhip (most popular music).
        """
        artist = (getattr(track, "artists", None) or [""])[0]
        title = getattr(track, "title", "") or ""
        if not artist or not title:
            return {}

        artist_slug = _to_slug(artist)
        title_slug = _to_slug(title)
        title_slug_clean = re.sub(r"-feat-.*$|-ft-.*$|-featuring-.*$", "", title_slug)

        # Also try with parenthetical collaboration credits stripped:
        # "YouUgly (with Westside Gunn)" → "YouUgly", "Glory (feat. Bas)" → "Glory"
        _COLLAB_RE = re.compile(
            r'\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^\)\]]+[\)\]]',
            re.IGNORECASE,
        )
        title_no_collab = _COLLAB_RE.sub("", title).strip()
        title_slug_no_collab = _to_slug(title_no_collab) if title_no_collab != title else title_slug

        for t_slug in dict.fromkeys([title_slug_no_collab, title_slug_clean, title_slug]):
            url = f"{_SONGWHIP_API}/{artist_slug}/{t_slug}"
            try:
                resp = self._get(url, timeout=8)
                if resp.status_code == 200:
                    return self._extract_songwhip(resp.json())
                logger.debug(f"[Songwhip] {resp.status_code} for {artist_slug}/{t_slug}")
            except Exception as e:
                logger.debug(f"[Songwhip] Request failed: {e}")

        return {}

    def _lookup_deezer_track_url_by_isrc(self, isrc: str) -> Optional[str]:
        api_url = f"https://api.deezer.com/track/isrc:{str(isrc).strip().upper()}"
        try:
            resp = self._get(api_url, timeout=8)
            if not resp.ok:
                logger.debug("[Deezer ISRC] HTTP %s for %s", resp.status_code, isrc)
                return None
            payload = resp.json()
        except Exception as e:
            logger.debug("[Deezer ISRC] Lookup failed for %s: %s", isrc, e)
            return None

        link = str(payload.get("link") or "").strip()
        if link:
            return link
        track_id = payload.get("id")
        if track_id:
            return f"https://www.deezer.com/track/{track_id}"
        return None

    def _try_deezer_songlink(self, track) -> dict[str, str]:
        isrc = getattr(track, "isrc", None)
        if not isrc:
            return {}

        deezer_url = self._lookup_deezer_track_url_by_isrc(isrc)
        if not deezer_url:
            return {}

        logger.debug(f"[Deezer->song.link] Found Deezer URL for '{track.title}': {deezer_url}")
        try:
            resp = self._get(
                _ODESLI_API,
                params={"url": deezer_url, "platform": "deezer", "type": "song"},
                timeout=8,
            )
            if not resp.ok:
                logger.debug("[Deezer->song.link] HTTP %s for '%s'", resp.status_code, track.title)
                return {}
            return self._extract_odesli(resp.json(), track.title)
        except Exception as e:
            logger.debug(f"[Deezer->song.link] Request failed for '{track.title}': {e}")
            return {}

    def _try_songstats(self, track) -> dict[str, str]:
        isrc = str(getattr(track, "isrc", "") or "").strip().upper()
        if not isrc:
            return {}

        page_url = f"https://songstats.com/{isrc}?ref=ISRCFinder"
        try:
            resp = self._get(page_url, timeout=10)
            if not resp.ok:
                logger.debug("[Songstats] HTTP %s for %s", resp.status_code, isrc)
                return {}
            body = resp.text
        except Exception as e:
            logger.debug("[Songstats] Request failed for %s: %s", isrc, e)
            return {}

        result: dict[str, str] = {}
        for match in _SONGSTATS_SCRIPT_RE.finditer(body):
            raw_script = html.unescape((match.group(1) or "").strip())
            if not raw_script:
                continue
            try:
                payload = json.loads(raw_script)
            except Exception:
                continue
            self._collect_songstats_links(payload, result)

        if result:
            logger.debug("[Songstats] Resolved %s for '%s': %s", isrc, getattr(track, "title", ""), list(result.keys()))
        return result

    def _collect_songstats_links(self, value, result: dict[str, str]) -> None:
        if isinstance(value, dict):
            same_as = value.get("sameAs")
            if same_as is not None:
                self._apply_songstats_links(same_as, result)
            for nested in value.values():
                self._collect_songstats_links(nested, result)
        elif isinstance(value, list):
            for nested in value:
                self._collect_songstats_links(nested, result)

    def _apply_songstats_links(self, value, result: dict[str, str]) -> None:
        if isinstance(value, str):
            self._assign_songstats_link(value, result)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    self._assign_songstats_link(item, result)

    def _assign_songstats_link(self, raw_link: str, result: dict[str, str]) -> None:
        link = (raw_link or "").strip()
        if not link:
            return
        if "music.amazon." in link and "amazonMusic" not in result:
            asin = _extract_amazon_asin(link)
            if asin:
                result["amazonMusic"] = asin
        elif "listen.tidal.com/track" in link and "tidal" not in result:
            match = re.search(r"/track/(\d+)", link)
            if match:
                result["tidal"] = match.group(1)
        elif "deezer.com" in link and "deezer" not in result:
            match = re.search(r"/track/(\d+)", link)
            if match:
                result["deezer"] = match.group(1)

    def _extract_songwhip(self, data: dict) -> dict[str, str]:
        links = data.get("data", {}).get("links", {})
        result: dict[str, str] = {}

        # Amazon Music — extract trackAsin, prefer US storefront
        for entry in links.get("amazonMusic", []):
            url = entry.get("link", "")
            countries = entry.get("countries")
            asin = _extract_amazon_asin(url)
            if asin:
                if countries is None or "US" in countries:
                    result["amazonMusic"] = asin
                    break
                result.setdefault("amazonMusic", asin)

        # Tidal
        for entry in links.get("tidal", []):
            m = re.search(r"/track/(\d+)", entry.get("link", ""))
            if m:
                result["tidal"] = m.group(1)
                break

        # Qobuz
        for entry in links.get("qobuz", []):
            m = re.search(r"/track/(\d+)", entry.get("link", ""))
            if m:
                result["qobuz"] = m.group(1)
                break

        # Apple Music
        for entry in links.get("itunes", []):
            m = re.search(r"[?&]i=(\d+)", entry.get("link", ""))
            if m:
                result["appleMusic"] = m.group(1)
                break

        # Deezer
        for entry in links.get("deezer", []):
            m = re.search(r"/track/(\d+)", entry.get("link", ""))
            if m:
                result["deezer"] = m.group(1)
                break

        return result

    # ── Odesli ────────────────────────────────────────────────────────────────

    def _try_odesli(self, track) -> dict[str, str]:
        """Odesli fallback with exponential backoff on 429."""
        params = self._build_odesli_params(track)
        if not params:
            logger.debug(f"[Odesli] No Spotify ID or ISRC for '{track.title}' — skipping.")
            return {}

        for attempt, delay in enumerate([0] + _ODESLI_RETRY_DELAYS):
            if delay:
                logger.debug(
                    f"[Odesli] Rate-limited — retrying in {delay}s "
                    f"(attempt {attempt}/{len(_ODESLI_RETRY_DELAYS)})..."
                )
                time.sleep(delay)
            try:
                resp = self._get(_ODESLI_API, params=params, timeout=8)
            except Exception as e:
                logger.debug(
                    "[Odesli] Request failed for '%s' (%s)",
                    track.title,
                    type(e).__name__,
                )
                return {}

            if resp.status_code == 429:
                continue
            if not resp.ok:
                logger.debug(f"[Odesli] HTTP {resp.status_code}")
                return {}

            try:
                data = resp.json()
            except Exception as e:
                logger.debug(f"[Odesli] JSON decode failed: {e}")
                return {}

            return self._extract_odesli(data, track.title)

        logger.debug(f"[Odesli] Gave up after all retries for '{track.title}'")
        return {}

    def _build_odesli_params(self, track) -> Optional[dict]:
        params: dict = {}
        if self._api_key:
            params["key"] = self._api_key

        spotify_id = getattr(track, "spotify_id", None)
        if spotify_id:
            params["url"] = f"https://open.spotify.com/track/{spotify_id}"
            params["platform"] = "spotify"
            params["type"] = "song"
            return params

        if getattr(track, "isrc", None):
            params["isrc"] = track.isrc
            params["country"] = "US"
            return params

        return None

    def _extract_odesli(self, data: dict, title: str) -> dict[str, str]:
        links = data.get("linksByPlatform", {})
        entities = data.get("entitiesByUniqueId", {})
        result: dict[str, str] = {}

        for platform, link_info in links.items():
            entity_uid = link_info.get("entityUniqueId", "")
            entity = entities.get(entity_uid, {})
            if platform == "amazonMusic":
                asin = (
                    _extract_amazon_asin(link_info.get("url", ""))
                    or _extract_amazon_asin(entity.get("id", ""))
                    or _extract_amazon_asin(entity_uid.split("::")[-1] if "::" in entity_uid else entity_uid)
                )
                if asin:
                    result[platform] = asin
                    logger.debug(f"[Odesli] '{title}' -> {platform}: {asin}")
                    continue
            raw_id = entity.get("id") or (entity_uid.split("::")[-1] if "::" in entity_uid else "")
            if raw_id:
                result[platform] = str(raw_id)
                logger.debug(f"[Odesli] '{title}' → {platform}: {raw_id}")

        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _store(
        self,
        cache_key: Optional[str],
        result: dict,
        missing_platforms: Iterable[str] = (),
    ) -> None:
        if cache_key:
            self._cache.put(cache_key, result, missing_platforms)
