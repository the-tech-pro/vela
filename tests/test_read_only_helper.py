import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antra.core.read_only_helper import (
    PROTOCOL_VERSION,
    _desktop_config,
    dispatch_read_only,
    serve_read_only_helper,
)
from antra.core.apple_library import AppleLibraryClient


class ReadOnlyHelperProtocolTests(unittest.TestCase):
    def test_frames_multiple_requests_and_preserves_ids(self):
        requests = [
            {
                "protocol_version": PROTOCOL_VERSION,
                "id": "request-1",
                "command": "echo",
                "params": {"value": "first"},
            },
            {
                "protocol_version": PROTOCOL_VERSION,
                "id": 2,
                "command": "echo",
                "params": {"value": "second"},
            },
        ]
        input_stream = io.StringIO(
            "".join(json.dumps(request) + "\n" for request in requests)
        )
        output_stream = io.StringIO()
        calls = []

        def dispatch(command, params, config_path):
            calls.append((command, params, config_path))
            return params["value"]

        result = serve_read_only_helper(
            "fixture-config.json",
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=io.StringIO(),
            dispatcher=dispatch,
        )

        self.assertEqual(result, 0)
        responses = [
            json.loads(line) for line in output_stream.getvalue().splitlines()
        ]
        self.assertEqual(
            responses,
            [
                {"id": "request-1", "ok": True, "result": "first"},
                {"id": 2, "ok": True, "result": "second"},
            ],
        )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[2] == "fixture-config.json" for call in calls))

    def test_invalid_frame_and_command_error_do_not_stop_server_or_log_secrets(self):
        input_stream = io.StringIO(
            "{not-json}\n"
            + json.dumps(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": "bad-command",
                    "command": "explode",
                    "params": {},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "id": "after-error",
                    "command": "ok",
                    "params": {},
                }
            )
            + "\n"
        )
        output_stream = io.StringIO()
        error_stream = io.StringIO()

        def dispatch(command, _params, _config_path):
            if command == "explode":
                raise RuntimeError(
                    "authorization=top-secret Bearer abc.def?token=hidden"
                )
            return {"alive": True}

        serve_read_only_helper(
            None,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=error_stream,
            dispatcher=dispatch,
        )

        responses = [
            json.loads(line) for line in output_stream.getvalue().splitlines()
        ]
        self.assertEqual(responses[0]["error"]["code"], "invalid_json")
        self.assertEqual(responses[1]["id"], "bad-command")
        self.assertEqual(responses[1]["error"]["code"], "command_failed")
        self.assertNotIn("top-secret", responses[1]["error"]["message"])
        self.assertNotIn("abc.def", responses[1]["error"]["message"])
        self.assertEqual(
            responses[2],
            {"id": "after-error", "ok": True, "result": {"alive": True}},
        )
        self.assertIn("RuntimeError", error_stream.getvalue())
        self.assertNotIn("top-secret", error_stream.getvalue())
        self.assertNotIn("hidden", error_stream.getvalue())

    def test_rejects_wrong_protocol_and_correlates_error(self):
        input_stream = io.StringIO(
            json.dumps(
                {
                    "protocol_version": 999,
                    "id": "version-check",
                    "command": "echo",
                }
            )
            + "\n"
        )
        output_stream = io.StringIO()
        serve_read_only_helper(
            None,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=io.StringIO(),
            dispatcher=lambda *_args: self.fail("dispatcher should not run"),
        )
        response = json.loads(output_stream.getvalue())
        self.assertEqual(response["id"], "version-check")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unsupported_protocol")

    def test_response_survives_legacy_windows_stdout_encoding(self):
        request = {
            "protocol_version": PROTOCOL_VERSION,
            "id": "unicode-response",
            "command": "library",
            "params": {},
        }
        input_stream = io.StringIO(json.dumps(request) + "\n")
        raw_output = io.BytesIO()
        output_stream = io.TextIOWrapper(raw_output, encoding="cp1252")

        serve_read_only_helper(
            None,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=io.StringIO(),
            dispatcher=lambda *_args: {"title": "Café 🎵"},
        )
        output_stream.flush()
        encoded_response = raw_output.getvalue()

        self.assertTrue(encoded_response.isascii())
        response = json.loads(encoded_response)
        self.assertEqual(response["result"]["title"], "Café 🎵")


class ReadOnlyHelperConfigTests(unittest.TestCase):
    def test_desktop_config_reloads_credentials_without_mutating_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "download_path": str(Path(temp_dir) / "Music"),
                        "download_path_is_library_root": True,
                        "apple_music_user_token": "first-token",
                        "download_sources": ["apple", "qobuz"],
                        "sources_enabled": ["apple"],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"APPLE_MUSIC_USER_TOKEN": "env-token"}):
                first = _desktop_config(str(config_path))
                config_path.write_text(
                    json.dumps(
                        {
                            "apple_music_user_token": "second-token",
                            "download_source": "auto",
                        }
                    ),
                    encoding="utf-8",
                )
                second = _desktop_config(str(config_path))

            self.assertEqual(first.apple_music_user_token, "first-token")
            self.assertEqual(first.source_preference, "apple,qobuz")
            self.assertEqual(first.sources_enabled, "apple")
            self.assertEqual(second.apple_music_user_token, "second-token")
            self.assertEqual(second.source_preference, "auto")

    def test_apple_library_dispatch_returns_compact_cached_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            cache_path = Path(temp_dir) / "apple_library_cache.sqlite3"
            config_path.write_text(
                json.dumps({
                    "apple_authorization_token": "Bearer helper-secret",
                    "apple_music_user_token": "helper-account",
                    "apple_storefront": "gb",
                }),
                encoding="utf-8",
            )
            with AppleLibraryClient(
                "Bearer helper-secret",
                "helper-account",
                "gb",
                cache_path=str(cache_path),
            ) as writer:
                writer._cache.set("library-index-v2", {
                    "saved_songs_count": 1,
                    "albums": [],
                    "playlists": [],
                    "details": {"large": {"tracks": [{"title": "Hidden"}]}},
                    "indexed_at": 42.0,
                })
                writer._cache.set("artist-index-v2", {
                    "artists": [{
                        "name": "Helper Artist",
                        "image_url": "",
                        "track_count": 1,
                    }],
                    "details": {
                        "helper artist": {"tracks": [{"title": "Hidden"}]}
                    },
                })

            with patch(
                "antra.core.apple_library.requests.Session.get",
                side_effect=AssertionError("helper used the network"),
            ):
                result = dispatch_read_only(
                    "apple_library",
                    {},
                    str(config_path),
                )

            self.assertEqual(result["saved_songs_count"], 1)
            self.assertEqual(result["artists"][0]["name"], "Helper Artist")
            self.assertNotIn("details", result)
            self.assertNotIn("artist_details", result)
            self.assertNotIn("helper-secret", json.dumps(result))
            self.assertTrue(result["revision"].startswith("sha256:"))
            cache_path.unlink()

    def test_apple_artist_dispatch_can_use_v2_cache_without_authorization(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            cache_path = Path(temp_dir) / "apple_library_cache.sqlite3"
            config_path.write_text(
                json.dumps({
                    "apple_authorization_token": "",
                    "apple_music_user_token": "helper-artist-account",
                    "apple_storefront": "gb",
                }),
                encoding="utf-8",
            )
            with AppleLibraryClient(
                "Bearer seed",
                "helper-artist-account",
                "gb",
                cache_path=str(cache_path),
            ) as writer:
                writer._cache.set("artist-detail-v2:cached artist", {
                    "name": "Cached Artist",
                    "content_type": "artist",
                    "track_count": 1,
                    "tracks": [{"title": "Cached Song"}],
                })

            with patch(
                "antra.core.apple_library.requests.Session.get",
                side_effect=AssertionError("artist helper used the network"),
            ):
                result = dispatch_read_only(
                    "apple_library_artist",
                    {"artist_name": "CACHED ARTIST"},
                    str(config_path),
                )

            self.assertEqual(result["tracks"][0]["title"], "Cached Song")
            self.assertTrue(result["from_cache"])
            cache_path.unlink()


if __name__ == "__main__":
    unittest.main()
