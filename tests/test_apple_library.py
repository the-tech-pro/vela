import unittest
import sys
import types
import tempfile
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import requests
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = MagicMock
    requests_stub.HTTPError = type("HTTPError", (Exception,), {})
    sys.modules["requests"] = requests_stub
    requests = requests_stub

from antra.core.apple_library import AppleLibraryClient


class AppleLibraryURLTests(unittest.TestCase):
    def setUp(self):
        self.client = AppleLibraryClient("Bearer test", "music-user-token", "gb")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": []}
        self.client._session.get = MagicMock(return_value=response)

    def tearDown(self):
        self.client.close()

    def test_library_relative_path_uses_library_base(self):
        self.client._get_json("/songs", {"limit": 1})
        url = self.client._session.get.call_args.args[0]
        self.assertEqual(url, "https://api.music.apple.com/v1/me/library/songs")

    def test_api_root_pagination_path_is_not_duplicated(self):
        self.client._get_json("/v1/me/library/songs?offset=100")
        url = self.client._session.get.call_args.args[0]
        self.assertEqual(url, "https://api.music.apple.com/v1/me/library/songs?offset=100")

    def test_parallel_collection_pages_are_reassembled_in_offset_order(self):
        def fake_get(path, params=None):
            offset = int((params or {}).get("offset", 0))
            return {
                "data": [{"id": str(index)} for index in range(offset, min(offset + 2, 6))],
                "meta": {"total": 6},
            }

        self.client._get_json = MagicMock(side_effect=fake_get)
        progress = []
        items = list(self.client._iter_collection(
            "/albums", {"limit": 2}, parallel=True,
            item_progress_callback=progress.append,
        ))
        self.assertEqual([item["id"] for item in items], ["0", "1", "2", "3", "4", "5"])
        self.assertEqual(progress[-1], 6)
        self.assertEqual(progress, sorted(progress))

    def test_non_playlist_resources_are_not_indexed_as_playlists(self):
        folder = {
            "id": "p.folder",
            "type": "library-playlist-folders",
            "attributes": {"name": "Folder"},
        }
        self.assertIsNone(self.client._playlist_summary(folder))

    def test_empty_catalog_playlist_uses_parent_resource_without_tracks_request(self):
        playlist_id = "p.8Wx66NrSV9GrB2A"
        catalog_id = "pl.u-AkAmm53C2JW9Dpq"
        catalog_href = f"/v1/catalog/us/playlists/{catalog_id}"
        self.client._storefront = "us"
        self.client._get_library_playlist_meta = MagicMock(
            return_value=("Untitled Playlist", None)
        )

        library_item = {
            "id": playlist_id,
            "type": "library-playlists",
            "attributes": {
                "name": "Untitled Playlist",
                "playParams": {"globalId": catalog_id},
            },
            "relationships": {
                "tracks": {
                    "href": f"/v1/catalog/us/playlists/{catalog_id}/tracks",
                },
                "catalog": {
                    "data": [{"id": catalog_id, "type": "playlists", "href": catalog_href}],
                },
            },
        }
        catalog_item = {
            "id": catalog_id,
            "type": "playlists",
            "attributes": {
                "name": "Untitled Playlist",
                "trackCount": 0,
            },
            "relationships": {"tracks": {"data": []}},
        }

        requested_paths = []

        def get_json(path, params=None):
            requested_paths.append((path, params))
            if path == f"/playlists/{playlist_id}":
                return {"data": [library_item]}
            if path == catalog_href:
                return {"data": [catalog_item]}
            raise AssertionError(f"Unexpected Apple path: {path}")

        self.client._get_json = MagicMock(side_effect=get_json)
        tracks = self.client.get_library_playlist_tracks(playlist_id, force_refresh=True)
        self.assertEqual(tracks, [])
        self.assertEqual([path for path, _ in requested_paths], [
            f"/playlists/{playlist_id}",
            catalog_href,
        ])
        self.assertTrue(all(not path.endswith("/tracks") for path, _ in requested_paths))
        self.assertEqual(requested_paths[1][1], {
            "include": "tracks",
            "limit[tracks]": 100,
        })

    def test_included_playlist_tracks_follow_relationship_pagination(self):
        playlist_id = "p.paginated"
        self.client._get_library_playlist_meta = MagicMock(return_value=("Paginated", None))
        first_track = {
            "id": "song-1",
            "attributes": {
                "name": "First",
                "artistName": "Artist",
                "albumName": "Album",
                "playParams": {"id": "song-1"},
            },
        }
        second_track = {
            "id": "song-2",
            "attributes": {
                "name": "Second",
                "artistName": "Artist",
                "albumName": "Album",
                "playParams": {"id": "song-2"},
            },
        }
        next_path = f"/v1/me/library/playlists/{playlist_id}/tracks?offset=1"

        def get_json(path, params=None):
            if path == f"/playlists/{playlist_id}":
                return {"data": [{
                    "id": playlist_id,
                    "type": "library-playlists",
                    "attributes": {"name": "Paginated", "trackCount": 2},
                    "relationships": {
                        "tracks": {"data": [first_track], "next": next_path, "meta": {"total": 2}},
                        "catalog": {"data": []},
                    },
                }]}
            if path == next_path:
                return {"data": [second_track]}
            raise AssertionError(f"Unexpected Apple path: {path}")

        self.client._get_json = MagicMock(side_effect=get_json)
        tracks = self.client.get_library_playlist_tracks(playlist_id, force_refresh=True)
        self.assertEqual([track.title for track in tracks], ["First", "Second"])

    def test_nonempty_catalog_playlist_without_tracks_fails_validation(self):
        playlist_id = "p.inconsistent"
        catalog_id = "pl.u-inconsistent"
        catalog_href = f"/v1/catalog/us/playlists/{catalog_id}"
        self.client._get_library_playlist_meta = MagicMock(return_value=("Inconsistent", None))

        def get_json(path, params=None):
            if path == f"/playlists/{playlist_id}":
                return {"data": [{
                    "id": playlist_id,
                    "type": "library-playlists",
                    "attributes": {"name": "Inconsistent", "playParams": {"globalId": catalog_id}},
                    "relationships": {
                        "catalog": {"data": [{"id": catalog_id, "href": catalog_href}]},
                    },
                }]}
            if path == catalog_href:
                return {"data": [{
                    "id": catalog_id,
                    "type": "playlists",
                    "attributes": {"name": "Inconsistent", "trackCount": 1},
                    "relationships": {"tracks": {"data": []}},
                }]}
            raise AssertionError(f"Unexpected Apple path: {path}")

        self.client._get_json = MagicMock(side_effect=get_json)
        with self.assertRaisesRegex(RuntimeError, "reported 1 playlist tracks but returned 0"):
            self.client.get_library_playlist_tracks(playlist_id, force_refresh=True)

    def test_library_index_is_persistent_and_account_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = str(Path(tmp) / "apple.sqlite3")
            with AppleLibraryClient(
                "Bearer test",
                "account-a",
                "gb",
                cache_path=cache_path,
            ) as first:
                first._get_saved_songs_count = MagicMock(return_value=12)
                first._get_all_albums = MagicMock(return_value=[{"id": "album"}])
                first._get_all_playlists = MagicMock(return_value=[{"id": "playlist"}])
                indexed = first.get_library(force_refresh=True)
                self.assertFalse(indexed["from_cache"])

            with AppleLibraryClient(
                "Bearer changed",
                "account-a",
                "gb",
                cache_path=cache_path,
            ) as second:
                second._get_saved_songs_count = MagicMock(
                    side_effect=AssertionError("network used")
                )
                cached = second.get_library()
                self.assertTrue(cached["from_cache"])
                self.assertEqual(cached["saved_songs_count"], 12)

    def test_cached_library_response_is_compact_and_network_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = str(Path(tmp) / "apple.sqlite3")
            album_url = "apple-music://library/album/album-1"
            with AppleLibraryClient(
                "Bearer super-secret",
                "secret-account-token",
                "gb",
                cache_path=cache_path,
            ) as client:
                client._cache.set("library-index-v2", {
                    "saved_songs_count": 0,
                    "albums": [{
                        "id": "album-1",
                        "name": "Album One",
                        "url": album_url,
                        "image_url": "https://example.invalid/1200x1200bb.jpg",
                        "track_count": 2,
                        "track_count_known": True,
                        "artist_name": "Artist One",
                    }],
                    "playlists": [],
                    "details": {"legacy": {"tracks": [{"title": "Do not return"}]}},
                    "artist_details": {
                        "artist one": {"tracks": [{"title": "Do not return"}]}
                    },
                    "indexed_at": 1234.5,
                })
                client._cache.set(f"detail-v2:{album_url}", {
                    "name": "Album One",
                    "url": album_url,
                    "tracks": [{"title": "Hidden Track"}],
                })
                client._cache.set("artist-index-v2", {
                    "artists": [{
                        "name": "Artist One",
                        "image_url": "https://example.invalid/1200x1200bb.jpg",
                        "track_count": 2,
                    }],
                    "details": {
                        "artist one": {"tracks": [{"title": "Hidden Artist Track"}]}
                    },
                })
                client._cache.set("artist-detail-v2:artist one", {
                    "name": "Artist One",
                    "tracks": [{"title": "Hidden Artist Track"}],
                })
                client._cache.set("full-index-state-v4", {
                    "complete": True,
                    "completed": 6,
                    "total": 6,
                    "percent": 100,
                    "release_completed": 1,
                    "release_total": 1,
                    "errors": [],
                    "indexed_at": 1200.0,
                    "target_urls": [album_url],
                    "target_signature": [[album_url, 2]],
                    "revision": "sha256:index-revision",
                })
                client._get_json = MagicMock(
                    side_effect=AssertionError("cached summary used the network")
                )
                client._cache.values_with_prefix = MagicMock(
                    side_effect=AssertionError("compact response scanned detail rows")
                )

                payload = client.get_library()
                second = client.get_library()
                on_demand = client.get_playlist_detail(album_url)

                self.assertTrue(payload["from_cache"])
                self.assertNotIn("details", payload)
                self.assertNotIn("artist_details", payload)
                self.assertEqual(payload["indexed_at"], 1234.5)
                self.assertTrue(payload["index_complete"])
                self.assertEqual(payload["index_progress"]["percent"], 100)
                self.assertEqual(payload["artists"], [{
                    "name": "Artist One",
                    "image_url": "https://example.invalid/316x316bb.jpg",
                    "track_count": 2,
                }])
                self.assertEqual(payload["revision"], second["revision"])
                self.assertTrue(payload["revision"].startswith("sha256:"))
                encoded = json.dumps(payload)
                self.assertNotIn("Hidden Track", encoded)
                self.assertNotIn("Hidden Artist Track", encoded)
                self.assertNotIn("super-secret", encoded)
                self.assertNotIn("secret-account-token", encoded)
                self.assertEqual(on_demand["tracks"][0]["title"], "Hidden Track")
                self.assertTrue(on_demand["from_cache"])
                client._get_json.assert_not_called()
                client._cache.values_with_prefix.assert_not_called()

    def test_force_refresh_replaces_summary_and_revision_is_deterministic(self):
        albums = [{
            "id": "album-1",
            "name": "Album One",
            "url": "apple-music://library/album/album-1",
            "track_count": 1,
            "track_count_known": True,
            "artist_name": "Artist",
        }]
        self.client._get_saved_songs_count = MagicMock(return_value=1)
        self.client._get_all_albums = MagicMock(return_value=albums)
        self.client._get_all_playlists = MagicMock(return_value=[])

        first = self.client.get_library(force_refresh=True)
        second = self.client.get_library(force_refresh=True)
        self.assertFalse(first["from_cache"])
        self.assertEqual(first["revision"], second["revision"])

        self.client._get_saved_songs_count.return_value = 2
        changed = self.client.get_library(force_refresh=True)
        self.assertNotEqual(first["revision"], changed["revision"])
        self.assertEqual(self.client._get_saved_songs_count.call_count, 3)

    def test_v2_artist_detail_hit_does_not_rebuild_or_use_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = str(Path(tmp) / "apple.sqlite3")
            with AppleLibraryClient(
                "Bearer test",
                "artist-account",
                "gb",
                cache_path=cache_path,
            ) as writer:
                writer._cache.set("artist-detail-v2:björk", {
                    "name": "Björk",
                    "index_key": "björk",
                    "content_type": "artist",
                    "track_count": 1,
                    "tracks": [{
                        "title": "Jóga",
                        "artist": "Björk",
                        "album": "Homogenic",
                    }],
                })

            with AppleLibraryClient(
                "",
                "artist-account",
                "gb",
                cache_path=cache_path,
                cache_only=True,
            ) as reader:
                reader._build_artist_index = MagicMock(
                    side_effect=AssertionError("artist index rebuilt")
                )
                reader._get_json = MagicMock(
                    side_effect=AssertionError("artist detail used the network")
                )

                detail = reader.get_artist_detail("BJÖRK")

                self.assertEqual(detail["name"], "Björk")
                self.assertEqual([track["title"] for track in detail["tracks"]], ["Jóga"])
                self.assertTrue(detail["from_cache"])
                reader._build_artist_index.assert_not_called()
                reader._get_json.assert_not_called()

    def test_cache_reuses_one_thread_safe_wal_connection_and_closes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "apple.sqlite3"
            with patch(
                "antra.core.apple_library.sqlite3.connect",
                wraps=sqlite3.connect,
            ) as connect:
                client = AppleLibraryClient(
                    "Bearer test",
                    "connection-account",
                    "gb",
                    cache_path=str(cache_file),
                )

                def round_trip(index):
                    key = f"parallel:{index}"
                    value = {"index": index}
                    client._cache.set(key, value)
                    return client._cache.get(key)["index"]

                with ThreadPoolExecutor(max_workers=8) as pool:
                    values = list(pool.map(round_trip, range(32)))

                self.assertEqual(values, list(range(32)))
                self.assertEqual(connect.call_count, 1)
                with client._cache._lock:
                    db = client._cache._connection()
                    self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
                client.close()
                client.close()

            cache_file.unlink()
            self.assertFalse(cache_file.exists())

    def test_playlist_summary_pages_are_parallel_and_results_are_ordered(self):
        barrier = threading.Barrier(2)
        worker_threads = set()
        worker_lock = threading.Lock()

        def playlist_item(identifier, name):
            return {
                "id": identifier,
                "type": "library-playlists",
                "attributes": {"name": name, "trackCount": 1},
            }

        pages = {
            0: [
                playlist_item(f"p.{index}", f"Playlist {index:03d}")
                for index in range(100)
            ],
            100: [
                playlist_item(f"p.{index}", f"Playlist {index:03d}")
                for index in range(100, 200)
            ],
            200: [playlist_item("p.200", "Playlist 200")],
        }

        def fake_get(path, params=None):
            self.assertEqual(path, "/playlists")
            offset = int((params or {}).get("offset", 0))
            if offset:
                with worker_lock:
                    worker_threads.add(threading.get_ident())
                barrier.wait(timeout=5)
            return {"data": pages[offset], "meta": {"total": 201}}

        self.client._get_json = MagicMock(side_effect=fake_get)
        playlists = self.client._get_all_playlists()

        self.assertEqual(
            [playlist["name"] for playlist in playlists],
            [f"Playlist {index:03d}" for index in range(201)],
        )
        self.assertEqual(len(worker_threads), 2)
        offsets = {
            int(
                (
                    call.kwargs.get("params")
                    if "params" in call.kwargs
                    else call.args[1] if len(call.args) > 1 else {}
                ).get("offset", 0)
            )
            for call in self.client._get_json.call_args_list
        }
        self.assertEqual(offsets, {0, 100, 200})

    def test_index_validation_reuses_in_memory_details(self):
        album_url = "apple-music://library/album/in-memory"
        self.client.get_library = MagicMock(return_value={
            "saved_songs_count": 0,
            "albums": [{
                "url": album_url,
                "name": "In Memory",
                "track_count": 2,
                "track_count_known": True,
            }],
            "playlists": [],
        })
        self.client.get_playlist_detail = MagicMock(
            return_value={"url": album_url, "tracks": [{}, {}]}
        )

        def cache_get(key):
            if key.startswith("detail-v2:"):
                raise AssertionError("validation re-read a detail row")
            return None

        self.client._cache.get = MagicMock(side_effect=cache_get)
        result = self.client.index_entire_library()

        self.assertTrue(result["complete"])
        self.assertEqual(result["completed"], result["total"])

    def test_full_index_progress_is_weighted_by_tracks_and_never_reports_100_early(self):
        self.client.get_library = MagicMock(return_value={
            "albums": [
                {"url": "apple-music://library/album/small", "name": "Small", "track_count": 10},
                {"url": "apple-music://library/album/large", "name": "Large", "track_count": 90},
            ],
            "playlists": [],
        })
        weights = {
            "apple-music://library/album/small": 10,
            "apple-music://library/album/large": 90,
        }

        def index_detail(url, _force_refresh, progress_callback):
            weight = weights[url]
            progress_callback(weight // 2)
            progress_callback(weight)
            return {"tracks": [{}] * weight}

        self.client.get_playlist_detail = MagicMock(side_effect=index_detail)
        events = []

        result = self.client.index_entire_library(progress_callback=events.append)

        self.assertTrue(result["complete"])
        self.assertEqual(result["completed"], 203)
        self.assertEqual(result["total"], 203)
        self.assertTrue(events)
        percentages = [event["percent"] for event in events]
        self.assertTrue(all(isinstance(percent, int) and 0 <= percent <= 99 for percent in percentages))
        self.assertEqual(percentages, sorted(percentages))
        self.assertGreater(len(set(percentages)), 3)

    def test_missing_playlist_count_is_resolved_before_progress_is_weighted(self):
        self.client.get_library = MagicMock(return_value={
            "saved_songs_count": 0,
            "albums": [],
            "playlists": [{
                "url": "apple-music://library/playlist/large",
                "name": "Large playlist",
                "track_count": 0,
            }],
        })
        self.client._get_target_track_count = MagicMock(return_value=250)

        def index_detail(_url, _force_refresh, progress_callback):
            progress_callback(100, 250)
            progress_callback(250, 250)
            return {"tracks": [{}] * 250}

        self.client.get_playlist_detail = MagicMock(side_effect=index_detail)
        events = []
        result = self.client.index_entire_library(progress_callback=events.append)

        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], 502)
        self.assertEqual(result["completed"], 502)
        self.assertEqual(events[0]["percent"], 0)
        self.assertTrue(all(event["percent"] <= 99 for event in events))

    def test_known_empty_playlist_does_not_launch_count_lookup(self):
        self.client.get_library = MagicMock(return_value={
            "saved_songs_count": 0,
            "albums": [],
            "playlists": [{
                "url": "apple-music://library/playlist/empty",
                "name": "Empty playlist",
                "track_count": 0,
                "track_count_known": True,
            }],
        })
        self.client._get_target_track_count = MagicMock(
            side_effect=AssertionError("known zero count was queried")
        )
        self.client.get_playlist_detail = MagicMock(return_value={"tracks": []})

        result = self.client.index_entire_library()

        self.assertTrue(result["complete"])
        self.client._get_target_track_count.assert_not_called()

    def test_playlist_summary_distinguishes_zero_from_omitted_count(self):
        known_empty = {
            "id": "p.empty",
            "type": "library-playlists",
            "attributes": {"name": "Empty", "trackCount": 0},
        }
        unknown = {
            "id": "p.unknown",
            "type": "library-playlists",
            "attributes": {"name": "Unknown"},
        }

        empty_summary = self.client._playlist_summary(known_empty)
        unknown_summary = self.client._playlist_summary(unknown)

        self.assertEqual(empty_summary["track_count"], 0)
        self.assertTrue(empty_summary["track_count_known"])
        self.assertEqual(unknown_summary["track_count"], 0)
        self.assertFalse(unknown_summary["track_count_known"])

    def test_artwork_urls_are_sized_for_the_display_surface(self):
        artwork = {
            "url": "https://example.invalid/{w}x{h}.jpg",
            "width": 3000,
            "height": 3000,
        }
        self.assertEqual(
            self.client._artwork_url(artwork, max_px=316),
            "https://example.invalid/316x316.jpg",
        )
        self.assertEqual(
            self.client._artwork_url(artwork, max_px=600),
            "https://example.invalid/600x600.jpg",
        )
        self.assertEqual(
            self.client._resize_cached_artwork_url(
                "https://is1-ssl.mzstatic.com/image/thumb/example/1200x1200bb.jpg",
                316,
            ),
            "https://is1-ssl.mzstatic.com/image/thumb/example/316x316bb.jpg",
        )


if __name__ == "__main__":
    unittest.main()
