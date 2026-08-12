import json
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

from antra.core.control import DownloadController
from antra.core.completed_files import completed_library_files, validated_library_file
from antra.core.engine import (
    DownloadEngine,
    EngineConfig,
    classify_download_failure,
)
from antra.core.events import EngineEvent, EngineEventType
from antra.core.models import DownloadResult, DownloadStatus, TrackMetadata
from antra.sources.base import BaseSourceAdapter, FailureCategory
from antra.utils.organizer import LibraryOrganizer


def make_track(index: int = 1) -> TrackMetadata:
    return TrackMetadata(
        title=f"Song {index}",
        artists=["Artist"],
        album="Album",
        isrc=f"TEST{index:08d}",
        track_number=index,
    )


class ProgressAdapter(BaseSourceAdapter):
    name = "progress-test"

    def is_available(self):
        return True

    def search(self, track):
        return None

    def download(self, result, output_path):
        raise NotImplementedError


class DownloadReliabilityTests(unittest.TestCase):
    def test_failure_classification(self):
        self.assertEqual(
            classify_download_failure("No matching source found"),
            FailureCategory.NO_MATCH,
        )
        self.assertEqual(
            classify_download_failure("503 Service Unavailable"),
            FailureCategory.TRANSIENT,
        )
        self.assertEqual(
            classify_download_failure("Authorization token expired"),
            FailureCategory.AUTH,
        )
        self.assertEqual(
            classify_download_failure("Unsupported DRM-protected format"),
            FailureCategory.UNSUPPORTED,
        )

    def test_repeated_recordings_receive_distinct_occurrence_ids(self):
        track = make_track()
        self.assertNotEqual(
            DownloadEngine._track_event_id(track, 0),
            DownloadEngine._track_event_id(track, 1),
        )

    def test_controller_uses_device_ceiling_for_live_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            control_path = Path(tmp) / "control.json"
            old_ceiling = os.environ.get("ANTRA_WORKER_CEILING")
            os.environ["ANTRA_WORKER_CEILING"] = "12"
            try:
                controller = DownloadController(str(control_path), initial_workers=16)
                self.assertEqual(controller.worker_state()["configured"], 12)
                control_path.write_text(
                    json.dumps({"max_workers": 10, "paused": False}),
                    encoding="utf-8",
                )
                controller._last_control_check = 0
                controller._refresh_external_state()
                self.assertEqual(controller.worker_state()["configured"], 10)
            finally:
                if old_ceiling is None:
                    os.environ.pop("ANTRA_WORKER_CEILING", None)
                else:
                    os.environ["ANTRA_WORKER_CEILING"] = old_ceiling

    def test_transient_failure_is_requeued_without_manual_retry(self):
        events = []
        organizer = MagicMock()
        engine = DownloadEngine(
            resolver=MagicMock(),
            organizer=organizer,
            config=EngineConfig(
                max_workers=1,
                auto_retry_window_seconds=0.5,
                auto_retry_backoff_seconds=0.01,
            ),
            event_callback=events.append,
        )
        calls = 0

        def download_once(track, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return DownloadResult(
                    track=track,
                    status=DownloadStatus.FAILED,
                    error_message="503 Service Unavailable",
                )
            engine._emit(EngineEventType.TRACK_COMPLETED, track=track)
            return DownloadResult(
                track=track,
                status=DownloadStatus.COMPLETED,
                file_path="song.flac",
            )

        engine.download_track = download_once
        result = engine.download_playlist([make_track()])

        self.assertEqual(calls, 2)
        self.assertEqual(result[0].status, DownloadStatus.COMPLETED)
        scheduled = [
            event for event in events
            if event.type == EngineEventType.TRACK_RETRY_SCHEDULED
        ]
        completed = [
            event for event in events
            if event.type == EngineEventType.TRACK_COMPLETED
        ]
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0].track_id, completed[0].track_id)
        organizer.mark_failed.assert_not_called()

    def test_terminal_auth_failure_is_not_retried(self):
        events = []
        organizer = MagicMock()
        engine = DownloadEngine(
            resolver=MagicMock(),
            organizer=organizer,
            config=EngineConfig(max_workers=1, auto_retry_window_seconds=1),
            event_callback=events.append,
        )
        engine.download_track = MagicMock(return_value=DownloadResult(
            track=make_track(),
            status=DownloadStatus.FAILED,
            error_message="Authorization token expired",
        ))

        result = engine.download_playlist([make_track()])

        self.assertEqual(result[0].status, DownloadStatus.FAILED)
        self.assertEqual(engine.download_track.call_count, 1)
        organizer.mark_failed.assert_called_once()
        self.assertFalse(any(
            event.type == EngineEventType.TRACK_RETRY_SCHEDULED
            for event in events
        ))

    def test_cancellation_stops_a_delayed_retry(self):
        controller = DownloadController(initial_workers=1)
        events = []

        def on_event(event):
            events.append(event)
            if event.type == EngineEventType.TRACK_RETRY_SCHEDULED:
                controller.cancel()

        engine = DownloadEngine(
            resolver=MagicMock(),
            organizer=MagicMock(),
            controller=controller,
            config=EngineConfig(
                auto_retry_window_seconds=300,
                auto_retry_backoff_seconds=5,
            ),
            event_callback=on_event,
        )
        engine.download_track = MagicMock(return_value=DownloadResult(
            track=make_track(),
            status=DownloadStatus.FAILED,
            error_message="503 Service Unavailable",
        ))

        result = engine.download_playlist([make_track()])

        self.assertEqual(engine.download_track.call_count, 1)
        self.assertEqual(result[0].status, DownloadStatus.CANCELLED)

    def test_stream_helper_reports_measured_bytes(self):
        class Response:
            headers = {"Content-Length": "6"}

            @staticmethod
            def iter_content(chunk_size=1):
                return iter((b"abc", b"def"))

        adapter = ProgressAdapter()
        progress = []
        adapter.set_download_progress_callback(
            lambda downloaded, total, phase: progress.append((downloaded, total, phase))
        )
        with tempfile.TemporaryDirectory() as tmp:
            destination = str(Path(tmp) / "audio.bin")
            adapter.write_stream_to_file(Response(), destination, 3)
            self.assertEqual(Path(destination).read_bytes(), b"abcdef")

        self.assertEqual(progress[-1], (6, 6, "transferring"))

    def test_organizer_state_writes_are_atomic_under_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            organizer = LibraryOrganizer(tmp)
            threads = []
            for index in range(12):
                track = make_track(index + 1)
                path = str(Path(tmp) / f"{index + 1}.flac")
                thread = threading.Thread(
                    target=organizer.mark_downloaded,
                    args=(track, path),
                )
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join(timeout=2)

            state = json.loads((Path(tmp) / ".antra_state.json").read_text(encoding="utf-8"))
            stored_tracks = [key for key in state if key.startswith("TRACK:")]
            self.assertGreaterEqual(len(stored_tracks), 12)

    def test_completed_paths_are_contained_existing_and_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            completed = root / "Album" / "song.flac"
            completed.parent.mkdir()
            completed.write_bytes(b"audio")
            missing = root / "missing.flac"
            outside = Path(tmp) / "provider-temp.flac"
            outside.write_bytes(b"temporary")
            results = [
                DownloadResult(
                    track=make_track(1),
                    status=DownloadStatus.COMPLETED,
                    file_path=str(completed),
                ),
                DownloadResult(
                    track=make_track(2),
                    status=DownloadStatus.COMPLETED,
                    file_path=str(missing),
                ),
                DownloadResult(
                    track=make_track(3),
                    status=DownloadStatus.COMPLETED,
                    file_path=str(outside),
                ),
                DownloadResult(
                    track=make_track(4),
                    status=DownloadStatus.SKIPPED,
                    file_path=str(completed),
                ),
            ]

            self.assertEqual(
                completed_library_files(results, str(root)),
                [os.path.normcase(os.path.realpath(completed))],
            )
            self.assertIsNone(validated_library_file(outside, str(root)))
            self.assertIsNone(validated_library_file(missing, str(root)))

    def test_completion_event_exposes_only_validated_final_path(self):
        from antra.json_cli import emit_event

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            final_path = root / "song.flac"
            final_path.write_bytes(b"audio")
            output = io.StringIO()
            with redirect_stdout(output):
                emit_event(
                    EngineEvent(
                        type=EngineEventType.TRACK_COMPLETED,
                        track=make_track(),
                        file_path=str(final_path),
                    ),
                    str(root),
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(
                payload["payload"]["final_file_path"],
                os.path.normcase(os.path.realpath(final_path)),
            )

            outside = Path(tmp) / "provider-temp.flac"
            outside.write_bytes(b"temp")
            output = io.StringIO()
            with redirect_stdout(output):
                emit_event(
                    EngineEvent(
                        type=EngineEventType.TRACK_COMPLETED,
                        track=make_track(),
                        file_path=str(outside),
                    ),
                    str(root),
                )
            self.assertIsNone(json.loads(output.getvalue())["payload"]["final_file_path"])


if __name__ == "__main__":
    unittest.main()
