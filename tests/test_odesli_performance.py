import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from antra.sources.odesli import OdesliEnricher, _SQLiteLinkCache


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def make_track(suffix: str = "1"):
    return SimpleNamespace(
        title=f"Song {suffix}",
        artists=["Artist"],
        album="Album",
        spotify_id=f"spotify-{suffix}",
        isrc=f"USABC26{int(suffix):05d}",
    )


class _RecordingSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.closed = False
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(3)
        self.max_active = 0
        self._active = 0

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append((url, kwargs))
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            self._barrier.wait(timeout=2)
            time.sleep(0.02)
            return object()
        finally:
            with self._lock:
                self._active -= 1

    def close(self):
        self.closed = True


class OdesliPipelinePerformanceTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        root = Path(self._temporary_directory.name)
        self.db_path = root / "link-cache.sqlite3"
        self.legacy_path = root / "link-cache.json"
        self._enrichers = []

    def make_enricher(self, **kwargs):
        enricher = OdesliEnricher(
            cache_db_path=str(self.db_path),
            legacy_cache_path=str(self.legacy_path),
            **kwargs,
        )
        self._enrichers.append(enricher)
        self.addCleanup(enricher.close)
        return enricher

    def test_merge_precedence_and_cached_completeness_are_deterministic(self):
        enricher = self.make_enricher()
        track = make_track("1")

        with (
            patch.object(
                enricher,
                "_try_odesli",
                return_value={"tidal": "tidal-odesli"},
            ) as odesli,
            patch.object(
                enricher,
                "_try_songwhip",
                return_value={
                    "tidal": "tidal-songwhip",
                    "appleMusic": "apple-songwhip",
                    "qobuz": "qobuz-songwhip",
                },
            ) as songwhip,
            patch.object(
                enricher,
                "_try_songstats",
                return_value={
                    "tidal": "tidal-songstats",
                    "amazonMusic": "amazon-songstats",
                },
            ) as songstats,
            patch.object(
                enricher,
                "_search_amazon",
                return_value="amazon-fuzzy",
            ),
            patch.object(enricher, "_try_deezer_songlink") as deezer,
            patch.object(enricher, "_try_itunes_odesli") as itunes,
        ):
            result = enricher.resolve(
                track,
                required_platforms={"amazonMusic", "appleMusic", "tidal"},
            )
            cached = enricher.resolve(
                track,
                required_platforms={"amazonMusic", "appleMusic", "tidal"},
            )

        self.assertEqual(result, cached)
        self.assertEqual(result["tidal"], "tidal-odesli")
        self.assertEqual(result["appleMusic"], "apple-songwhip")
        self.assertEqual(result["amazonMusic"], "amazon-songstats")
        self.assertEqual(result["qobuz"], "qobuz-songwhip")
        self.assertEqual(odesli.call_count, 1)
        self.assertEqual(songwhip.call_count, 1)
        self.assertEqual(songstats.call_count, 1)
        deezer.assert_not_called()
        itunes.assert_not_called()

    def test_enabled_platform_completeness_stops_unneeded_fallbacks(self):
        enricher = self.make_enricher()
        track = make_track("2")
        with (
            patch.dict(
                os.environ,
                {
                    "SOURCES_ENABLED": "amazon",
                    "SOURCE_PREFERENCES": "amazon",
                },
            ),
            patch.object(
                enricher,
                "_try_odesli",
                return_value={
                    "amazonMusic": "B000000002",
                    "tidal": "optional-tidal",
                },
            ),
            patch.object(enricher, "_try_songwhip") as songwhip,
            patch.object(enricher, "_try_songstats") as songstats,
            patch.object(enricher, "_try_deezer_songlink") as deezer,
            patch.object(enricher, "_try_itunes_odesli") as itunes,
            patch.object(enricher, "_search_amazon") as amazon,
        ):
            result = enricher.resolve(track)

        self.assertEqual(result["amazonMusic"], "B000000002")
        songwhip.assert_not_called()
        songstats.assert_not_called()
        deezer.assert_not_called()
        itunes.assert_not_called()
        amazon.assert_not_called()

    def test_songwhip_songstats_and_amazon_run_in_parallel(self):
        enricher = self.make_enricher()
        track = make_track("3")
        barrier = threading.Barrier(3)
        started = []
        started_lock = threading.Lock()

        def parallel_result(name, result):
            with started_lock:
                started.append(name)
            barrier.wait(timeout=2)
            time.sleep(0.05)
            return result

        with (
            patch.object(enricher, "_try_odesli", return_value={}),
            patch.object(
                enricher,
                "_try_songwhip",
                side_effect=lambda _: parallel_result(
                    "songwhip",
                    {"appleMusic": "apple-parallel"},
                ),
            ),
            patch.object(
                enricher,
                "_try_songstats",
                side_effect=lambda _: parallel_result(
                    "songstats",
                    {"tidal": "tidal-parallel"},
                ),
            ),
            patch.object(
                enricher,
                "_search_amazon",
                side_effect=lambda _: parallel_result(
                    "amazon",
                    "B000000003",
                ),
            ),
            patch.object(enricher, "_try_deezer_songlink", return_value={}),
            patch.object(enricher, "_try_itunes_odesli", return_value={}),
        ):
            started_at = time.perf_counter()
            result = enricher.resolve(
                track,
                required_platforms={"amazonMusic", "appleMusic", "tidal"},
            )
            elapsed = time.perf_counter() - started_at

        self.assertEqual(set(started), {"songwhip", "songstats", "amazon"})
        self.assertLess(elapsed, 0.4)
        self.assertEqual(
            result,
            {
                "appleMusic": "apple-parallel",
                "tidal": "tidal-parallel",
                "amazonMusic": "B000000003",
            },
        )

    def test_same_track_concurrent_resolutions_are_singleflight(self):
        enricher = self.make_enricher(fallback_workers=3)
        track = make_track("9")
        calls = 0
        calls_lock = threading.Lock()

        def exact_lookup(_track):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.08)
            return {
                "amazonMusic": "B000000009",
                "appleMusic": "apple-9",
                "tidal": "tidal-9",
            }

        with patch.object(enricher, "_try_odesli", side_effect=exact_lookup):
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(
                    lambda platform: enricher.resolve(
                        track,
                        required_platforms={platform},
                    ),
                    ("amazonMusic", "appleMusic", "tidal"),
                ))

        self.assertEqual(calls, 1)
        self.assertTrue(all(result["appleMusic"] == "apple-9" for result in results))
        self.assertTrue(all(result["amazonMusic"] == "B000000009" for result in results))
        self.assertTrue(all(result["tidal"] == "tidal-9" for result in results))

    def test_service_shares_one_enricher_across_enabled_adapters(self):
        from antra.core.config import Config
        from antra.core.service import AntraService
        from antra.sources.amazon import AmazonAdapter
        from antra.sources.apple import AppleAdapter

        cfg = Config(
            sources_enabled="apple,amazon",
            apple_enabled=True,
            amazon_enabled=True,
            output_format="lossless",
        )
        shared = object()
        with (
            patch(
                "antra.core.endpoint_manifest.load_endpoint_manifest",
                return_value=None,
            ),
            patch(
                "antra.core.service._fetch_gist_apple_mirror",
                return_value="",
            ),
            patch(
                "antra.sources.odesli.OdesliEnricher",
                return_value=shared,
            ) as constructor,
            patch.object(AppleAdapter, "is_available", return_value=True),
            patch.object(AmazonAdapter, "is_available", return_value=True),
        ):
            adapters = AntraService(cfg).build_adapters(cfg)

        self.assertEqual([adapter.name for adapter in adapters], ["apple", "amazon"])
        self.assertIs(adapters[0]._odesli, shared)
        self.assertIs(adapters[1]._odesli, shared)
        constructor.assert_called_once_with(api_key=None)

    def test_negative_cache_suppresses_only_missing_platforms_until_ttl(self):
        now = [1000.0]
        enricher = self.make_enricher(
            negative_ttl=5.0,
            clock=lambda: now[0],
        )
        track = make_track("4")

        with (
            patch.object(enricher, "_try_odesli", return_value={}) as odesli,
            patch.object(enricher, "_try_songwhip", return_value={}),
            patch.object(enricher, "_try_songstats", return_value={}),
            patch.object(enricher, "_try_deezer_songlink", return_value={}),
            patch.object(enricher, "_try_itunes_odesli", return_value={}),
            patch.object(enricher, "_search_amazon", return_value=None),
        ):
            self.assertEqual(
                enricher.resolve(track, required_platforms={"amazonMusic"}),
                {},
            )
            self.assertEqual(
                enricher.resolve(track, required_platforms={"amazonMusic"}),
                {},
            )
            self.assertEqual(odesli.call_count, 1)

            # A miss for Amazon must not suppress a newly required Apple lookup.
            self.assertEqual(
                enricher.resolve(track, required_platforms={"appleMusic"}),
                {},
            )
            self.assertEqual(odesli.call_count, 2)

            now[0] += 6.0
            self.assertEqual(
                enricher.resolve(track, required_platforms={"amazonMusic"}),
                {},
            )
            self.assertEqual(odesli.call_count, 3)

        # Expiry cleanup must commit before the refreshed negative result is
        # stored; otherwise SQLite keeps an open transaction and the result
        # survives only in this process's volatile fallback.
        enricher.close()
        reopened = self.make_enricher(
            negative_ttl=5.0,
            clock=lambda: now[0],
        )
        with patch.object(
            reopened,
            "_try_odesli",
            side_effect=AssertionError("refreshed miss was not persisted"),
        ):
            self.assertEqual(
                reopened.resolve(track, required_platforms={"amazonMusic"}),
                {},
            )

    def test_legacy_json_migrates_once_without_credentials(self):
        spotify_id = "0123456789ABCDEFGHIJKL"
        original_payload = {
            spotify_id: {
                "tidal": "12345",
                "api_key": "must-not-be-persisted",
            },
            "credential-shaped-legacy-key": {
                "amazonMusic": "B000000005",
            },
        }
        self.legacy_path.write_text(
            json.dumps(original_payload),
            encoding="utf-8",
        )

        enricher = self.make_enricher()
        track = make_track("5")
        track.spotify_id = spotify_id
        with patch.object(
            enricher,
            "_try_odesli",
            side_effect=AssertionError("migrated cache should satisfy lookup"),
        ):
            self.assertEqual(
                enricher.resolve(track, required_platforms={"tidal"}),
                {"tidal": "12345"},
            )
        enricher.close()

        self.assertEqual(
            json.loads(self.legacy_path.read_text(encoding="utf-8")),
            original_payload,
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT cache_key, result_json FROM link_cache"
            ).fetchall()
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertEqual(rows, [(spotify_id, '{"tidal":"12345"}')])
        self.assertNotIn("must-not-be-persisted", repr(rows))

        self.legacy_path.unlink()
        reopened = self.make_enricher()
        self.assertEqual(
            reopened.resolve(track, required_platforms={"tidal"}),
            {"tidal": "12345"},
        )

    def test_committed_batch_survives_crash_and_uncommitted_batch_rolls_back(self):
        script = r"""
import os
import sqlite3
import sys
import time
from antra.sources.odesli import _SQLiteLinkCache

db_path, legacy_path = sys.argv[1:3]
cache = _SQLiteLinkCache(
    db_path,
    legacy_path,
    negative_ttl=30.0,
    clock=time.time,
)
cache.put("atomic-key", {"tidal": "stable"}, {"amazonMusic"})
cache.close()

connection = sqlite3.connect(db_path, isolation_level=None)
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "UPDATE link_cache SET result_json = ? WHERE cache_key = ?",
    ('{"tidal":"torn"}', "atomic-key"),
)
connection.execute(
    "DELETE FROM link_cache_misses WHERE cache_key = ?",
    ("atomic-key",),
)
os._exit(0)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(self.db_path),
                str(self.legacy_path),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        cache = _SQLiteLinkCache(
            str(self.db_path),
            str(self.legacy_path),
            negative_ttl=30.0,
            clock=time.time,
        )
        self.addCleanup(cache.close)
        hit, value, misses = cache.get("atomic-key")
        self.assertTrue(hit)
        self.assertEqual(value, {"tidal": "stable"})
        self.assertEqual(misses, frozenset({"amazonMusic"}))

    def test_concurrent_process_stores_tolerate_wal_lock_contention(self):
        script = r"""
import sys
import time
from antra.sources.odesli import _SQLiteLinkCache

db_path, legacy_path, worker = sys.argv[1:4]
cache = _SQLiteLinkCache(
    db_path,
    legacy_path,
    negative_ttl=30.0,
    clock=time.time,
)
for index in range(25):
    key = f"{worker}-{index}"
    cache.put(key, {"tidal": key}, {"amazonMusic"})
cache.put("shared-key", {"tidal": worker}, {"amazonMusic"})
cache.close()
"""
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(self.db_path),
                    str(self.legacy_path),
                    str(worker),
                ],
                cwd=REPOSITORY_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for worker in range(4)
        ]
        failures = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            if process.returncode:
                failures.append((process.returncode, stdout, stderr))
        self.assertEqual(failures, [])

        with closing(sqlite3.connect(self.db_path)) as connection:
            unique_rows = connection.execute(
                "SELECT COUNT(*) FROM link_cache WHERE cache_key != 'shared-key'"
            ).fetchone()[0]
            shared = json.loads(
                connection.execute(
                    "SELECT result_json FROM link_cache WHERE cache_key = 'shared-key'"
                ).fetchone()[0]
            )
            miss_rows = connection.execute(
                "SELECT COUNT(*) FROM link_cache_misses"
            ).fetchone()[0]
        self.assertEqual(unique_rows, 100)
        self.assertIn(shared["tidal"], {"0", "1", "2", "3"})
        self.assertEqual(miss_rows, 101)

    def test_one_session_is_reused_across_bounded_parallel_requests(self):
        session = _RecordingSession()
        with patch(
            "antra.sources.odesli.requests.Session",
            return_value=session,
        ) as session_factory:
            enricher = self.make_enricher()

        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = list(
                executor.map(
                    lambda index: enricher._get(
                        f"https://example.invalid/{index}",
                        timeout=1,
                    ),
                    range(3),
                )
            )

        self.assertEqual(len(responses), 3)
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(session.max_active, 3)
        session_factory.assert_called_once_with()
        enricher.close()
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()
