import unittest
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = MagicMock
    sys.modules["requests"] = requests_stub

from antra.core.apple_library import AppleLibraryClient


class AppleLibraryURLTests(unittest.TestCase):
    def setUp(self):
        self.client = AppleLibraryClient("Bearer test", "music-user-token", "gb")
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": []}
        self.client._session.get = MagicMock(return_value=response)

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

    def test_library_index_is_persistent_and_account_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = str(Path(tmp) / "apple.sqlite3")
            first = AppleLibraryClient("Bearer test", "account-a", "gb", cache_path=cache_path)
            first._get_saved_songs_count = MagicMock(return_value=12)
            first._get_all_albums = MagicMock(return_value=[{"id": "album"}])
            first._get_all_playlists = MagicMock(return_value=[{"id": "playlist"}])
            indexed = first.get_library(force_refresh=True)
            self.assertFalse(indexed["from_cache"])

            second = AppleLibraryClient("Bearer changed", "account-a", "gb", cache_path=cache_path)
            second._get_saved_songs_count = MagicMock(side_effect=AssertionError("network used"))
            cached = second.get_library()
            self.assertTrue(cached["from_cache"])
            self.assertEqual(cached["saved_songs_count"], 12)

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
        self.assertEqual(result["completed"], 102)
        self.assertEqual(result["total"], 102)
        self.assertTrue(events)
        percentages = [event["percent"] for event in events]
        self.assertTrue(all(0 <= percent <= 99.9 for percent in percentages))
        self.assertEqual(percentages, sorted(percentages))
        self.assertGreater(len(set(percentages)), 3)


if __name__ == "__main__":
    unittest.main()
