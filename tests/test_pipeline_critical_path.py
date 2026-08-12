import os
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from antra.core.engine import DownloadEngine, EngineConfig
from antra.core.events import EngineEventType
from antra.core.models import (
    AudioFormat,
    DownloadStatus,
    SearchResult,
    TrackMetadata,
)
from antra.utils.tagger import FileTagger


_AUDIO_BYTES = b"fixture-audio-bytes-unchanged"
_PLAIN_LYRICS = "Fixture plain lyrics"
_SYNCED_LYRICS = "[00:00.00]Fixture synced lyrics"


def make_track(**overrides) -> TrackMetadata:
    values = {
        "title": "Fixture Song",
        "artists": ["Fixture Artist"],
        "album": "Initial Album",
        "duration_ms": 30_000,
        "isrc": "GBVEL2600042",
        "genres": ["Fixture Genre"],
    }
    values.update(overrides)
    return TrackMetadata(**values)


def make_search_result() -> SearchResult:
    return SearchResult(
        source="fixture",
        title="Fixture Song",
        artists=["Fixture Artist"],
        album="Hydrated Album",
        duration_ms=30_000,
        audio_format=AudioFormat.FLAC,
        quality_kbps=None,
        is_lossless=True,
        download_url="fixture://audio",
        stream_id="fixture",
        bit_depth=24,
        sample_rate_hz=96_000,
        source_metadata={},
    )


def fake_audio_info(
    *,
    codec: str = "flac",
    bit_depth: int = 24,
    sample_rate: int = 96_000,
    duration: float = 30.0,
):
    return SimpleNamespace(
        info=SimpleNamespace(
            codec=codec,
            bits_per_sample=bit_depth,
            sample_rate=sample_rate,
            bitrate=2_304_000,
            channels=2,
            length=duration,
        )
    )


class FixtureAdapter:
    name = "fixture"

    def __init__(self, output_path: Path, transfer_finished: threading.Event | None = None):
        self.output_path = output_path
        self.transfer_finished = transfer_finished
        self.progress_callback = None

    def hydrate_track_metadata(self, track, _result):
        track.album = "Hydrated Album"

    def set_download_progress_callback(self, callback):
        self.progress_callback = callback

    def download(self, _result, _output_base):
        self.output_path.write_bytes(_AUDIO_BYTES)
        if self.transfer_finished:
            self.transfer_finished.set()
        return str(self.output_path)

    def mark_failed_result(self, _result, _error):
        return None

    def should_retry_download(self, _result, _error):
        return False

    def should_exclude_adapter_after_failure(self, _result, _error):
        return True


class FixtureResolver:
    def __init__(self, resolution, resolve_hook=None):
        self.resolution = resolution
        self.resolve_hook = resolve_hook
        self.resolve_calls = 0
        self.successes = []

    def resolve(self, track, excluded_adapters=None):
        self.resolve_calls += 1
        if self.resolve_hook:
            self.resolve_hook(track)
        return self.resolution

    def last_resolve_report(self):
        return {}

    def record_outcome(self, *_args):
        return None

    def record_album_source_success(self, *args, **kwargs):
        self.successes.append((args, kwargs))

    def record_album_source_failure(self, *_args):
        return None


class CapturingTagger:
    def __init__(self):
        self.called = threading.Event()
        self.values = None

    def tag(self, _file_path, track):
        self.values = (track.lyrics, track.synced_lyrics, track.album)
        self.called.set()
        return True

    def save_cover_art_sidecar(self, _file_path, _track):
        return None


def make_organizer(output_path: Path):
    organizer = MagicMock()
    organizer.has_exact_output.return_value = False
    organizer.is_already_downloaded.return_value = None
    organizer.get_output_path.return_value = str(output_path.with_suffix(""))
    return organizer


class PipelineLyricsTests(unittest.TestCase):
    def test_lyrics_overlap_resolve_and_transfer_without_mutating_resolver_input(self):
        class BlockingLyricsFetcher:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.snapshot_ids = []
                self.snapshot_albums = []

            def fetch(self, track):
                self.snapshot_ids.append(id(track))
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise TimeoutError("test did not release lyrics")
                self.snapshot_albums.append(track.album)
                return _PLAIN_LYRICS, _SYNCED_LYRICS

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "song.flac"
            transfer_finished = threading.Event()
            fetcher = BlockingLyricsFetcher()
            track = make_track()
            result_data = make_search_result()
            adapter = FixtureAdapter(output_path, transfer_finished)

            def resolve_hook(resolver_track):
                self.assertTrue(fetcher.started.wait(timeout=1))
                self.assertIs(resolver_track, track)
                self.assertIsNone(resolver_track.lyrics)
                self.assertIsNone(resolver_track.synced_lyrics)

            resolver = FixtureResolver((result_data, adapter), resolve_hook)
            events = []
            engine = DownloadEngine(
                resolver=resolver,
                organizer=make_organizer(output_path),
                lyrics_fetcher=fetcher,
                config=EngineConfig(
                    max_retries=1,
                    fetch_lyrics=True,
                    save_cover_art_sidecar=False,
                    output_format="source",
                ),
                event_callback=events.append,
            )
            capturing_tagger = CapturingTagger()
            engine.tagger = capturing_tagger

            try:
                with (
                    patch(
                        "antra.core.engine.MutagenFile",
                        return_value=fake_audio_info(),
                    ) as mutagen,
                    patch(
                        "antra.core.metadata_enricher.MetadataEnricher.enrich",
                        return_value=None,
                    ),
                    ThreadPoolExecutor(max_workers=1) as pool,
                ):
                    future = pool.submit(engine.download_track, track, 1, 1)
                    self.assertTrue(transfer_finished.wait(timeout=1))
                    self.assertFalse(capturing_tagger.called.is_set())
                    self.assertIsNone(track.lyrics)
                    self.assertEqual(output_path.read_bytes(), _AUDIO_BYTES)
                    fetcher.release.set()
                    result = future.result(timeout=2)

                self.assertEqual(result.status, DownloadStatus.COMPLETED)
                self.assertEqual(result.audio_format, AudioFormat.FLAC)
                self.assertEqual(output_path.read_bytes(), _AUDIO_BYTES)
                self.assertEqual(
                    capturing_tagger.values,
                    (_PLAIN_LYRICS, _SYNCED_LYRICS, "Hydrated Album"),
                )
                self.assertNotEqual(fetcher.snapshot_ids, [id(track)])
                self.assertEqual(fetcher.snapshot_albums, ["Initial Album"])
                self.assertEqual(mutagen.call_count, 1)

                completed = [
                    event for event in events
                    if event.type == EngineEventType.TRACK_COMPLETED
                ]
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0].quality_label, "FLAC 24-bit/96kHz")
            finally:
                fetcher.release.set()

    def test_failed_initial_lyrics_attempt_retries_with_hydrated_snapshot(self):
        class RetryLyricsFetcher:
            def __init__(self):
                self.albums = []

            def fetch(self, track):
                self.albums.append(track.album)
                if len(self.albums) == 1:
                    raise ConnectionError("transient lyric provider failure")
                return _PLAIN_LYRICS, _SYNCED_LYRICS

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "song.flac"
            fetcher = RetryLyricsFetcher()
            adapter = FixtureAdapter(output_path)
            engine = DownloadEngine(
                resolver=FixtureResolver((make_search_result(), adapter)),
                organizer=make_organizer(output_path),
                lyrics_fetcher=fetcher,
                config=EngineConfig(
                    max_retries=1,
                    save_cover_art_sidecar=False,
                    output_format="source",
                ),
            )
            capturing_tagger = CapturingTagger()
            engine.tagger = capturing_tagger
            with (
                patch("antra.core.engine.MutagenFile", return_value=fake_audio_info()),
                patch(
                    "antra.core.metadata_enricher.MetadataEnricher.enrich",
                    return_value=None,
                ),
            ):
                result = engine.download_track(make_track())

            self.assertEqual(result.status, DownloadStatus.COMPLETED)
            self.assertEqual(fetcher.albums, ["Initial Album", "Hydrated Album"])
            self.assertEqual(
                capturing_tagger.values[:2],
                (_PLAIN_LYRICS, _SYNCED_LYRICS),
            )

    def test_terminal_track_failure_cancels_lyrics_without_late_mutation(self):
        class BlockingLyricsFetcher:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()
                self.finished = threading.Event()

            def fetch(self, _track):
                self.started.set()
                self.release.wait(timeout=5)
                self.finished.set()
                return _PLAIN_LYRICS, _SYNCED_LYRICS

        fetcher = BlockingLyricsFetcher()
        track = make_track()

        def resolve_hook(_track):
            self.assertTrue(fetcher.started.wait(timeout=1))

        engine = DownloadEngine(
            resolver=FixtureResolver(None, resolve_hook),
            organizer=make_organizer(Path("unused.flac")),
            lyrics_fetcher=fetcher,
            config=EngineConfig(max_retries=1),
        )

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(engine.download_track, track)
                result = future.result(timeout=1)
            self.assertEqual(result.status, DownloadStatus.FAILED)
            self.assertIsNone(track.lyrics)
            self.assertIsNone(track.synced_lyrics)
        finally:
            fetcher.release.set()
            self.assertTrue(fetcher.finished.wait(timeout=1))

        self.assertIsNone(track.lyrics)
        self.assertIsNone(track.synced_lyrics)


class ArtworkCacheTests(unittest.TestCase):
    class Response:
        content = b"raw-image-content"
        headers = {"Content-Type": "image/jpeg"}

        @staticmethod
        def raise_for_status():
            return None

    def test_concurrent_album_art_is_singleflight_and_sidecar_uses_exact_embed_bytes(self):
        tagger = FileTagger(artwork_cache_entries=2)
        request_started = threading.Event()
        release_request = threading.Event()
        prepared = b"normalized-artwork-bytes"

        def get_artwork(_url, timeout):
            self.assertEqual(timeout, 10)
            request_started.set()
            self.assertTrue(release_request.wait(timeout=2))
            return self.Response()

        with (
            patch("antra.utils.tagger.requests.get", side_effect=get_artwork) as get,
            patch.object(
                tagger,
                "_normalize_artwork",
                return_value=(prepared, "image/jpeg", 1000, 1000, 24),
            ) as normalize,
            ThreadPoolExecutor(max_workers=8) as pool,
        ):
            futures = [
                pool.submit(tagger._fetch_artwork, "https://art.test/album")
                for _ in range(8)
            ]
            self.assertTrue(request_started.wait(timeout=1))
            release_request.set()
            results = [future.result(timeout=2) for future in futures]

            with tempfile.TemporaryDirectory() as tmp:
                audio_path = Path(tmp) / "song.flac"
                audio_path.write_bytes(_AUDIO_BYTES)
                sidecar = tagger.save_cover_art_sidecar(
                    str(audio_path),
                    make_track(artwork_url="https://art.test/album"),
                )
                self.assertEqual(Path(sidecar).read_bytes(), prepared)

        self.assertEqual(get.call_count, 1)
        self.assertEqual(normalize.call_count, 1)
        self.assertTrue(all(result[0] is results[0][0] for result in results))
        self.assertIs(
            tagger._fetch_raw_artwork("https://art.test/album")[0],
            results[0][0],
        )

    def test_identical_content_from_distinct_urls_is_decoded_once(self):
        tagger = FileTagger()
        decode_started = threading.Event()
        release_decode = threading.Event()

        def normalize(_data, _mime):
            decode_started.set()
            self.assertTrue(release_decode.wait(timeout=2))
            return b"same-prepared-bytes", "image/jpeg", 10, 10, 24

        with (
            patch("antra.utils.tagger.requests.get", return_value=self.Response()) as get,
            patch.object(tagger, "_normalize_artwork", side_effect=normalize) as decode,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(tagger._fetch_artwork, "https://art.test/one")
            self.assertTrue(decode_started.wait(timeout=1))
            second = pool.submit(tagger._fetch_artwork, "https://art.test/two")
            release_decode.set()
            first_result = first.result(timeout=2)
            second_result = second.result(timeout=2)

        self.assertEqual(get.call_count, 2)
        self.assertEqual(decode.call_count, 1)
        self.assertIs(first_result[0], second_result[0])

    def test_failed_concurrent_artwork_request_retries_only_once_per_url(self):
        tagger = FileTagger()
        with (
            patch(
                "antra.utils.tagger.requests.get",
                side_effect=ConnectionError("art CDN unavailable"),
            ) as get,
            patch("antra.utils.tagger.time.sleep", return_value=None),
            ThreadPoolExecutor(max_workers=6) as pool,
        ):
            results = list(
                pool.map(
                    tagger._fetch_artwork,
                    ["https://art.test/failure"] * 6,
                )
            )

        self.assertEqual(results, [None] * 6)
        self.assertEqual(get.call_count, 3)

    def test_artwork_url_and_content_caches_are_bounded_lru(self):
        tagger = FileTagger(artwork_cache_entries=2)

        def response_for(url, timeout):
            self.assertEqual(timeout, 10)
            response = self.Response()
            response.content = url.encode("utf-8")
            return response

        def normalize(data, _mime):
            return data, "image/jpeg", 1, 1, 24

        with (
            patch("antra.utils.tagger.requests.get", side_effect=response_for) as get,
            patch.object(tagger, "_normalize_artwork", side_effect=normalize),
        ):
            for suffix in ("one", "two", "three"):
                tagger._fetch_artwork(f"https://art.test/{suffix}")

            self.assertEqual(len(tagger._artwork_cache), 2)
            self.assertEqual(len(tagger._artwork_content_cache), 2)
            tagger._fetch_artwork("https://art.test/one")
            self.assertEqual(get.call_count, 4)


class AudioProbeCacheTests(unittest.TestCase):
    @staticmethod
    def make_engine():
        return DownloadEngine(
            resolver=MagicMock(),
            organizer=MagicMock(),
            config=EngineConfig(fetch_lyrics=False),
        )

    def test_probe_values_drive_unchanged_decisions_and_invalidate_by_fingerprint(self):
        engine = self.make_engine()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.m4a"
            path.write_bytes(_AUDIO_BYTES)
            initial_bytes = path.read_bytes()
            probe_audio = fake_audio_info(codec="alac")

            with (
                patch("antra.core.engine.MutagenFile", return_value=probe_audio) as mutagen,
                patch.object(
                    engine,
                    "_probe_duration_seconds_with_ffprobe",
                    return_value=30.0,
                ) as ffprobe,
            ):
                self.assertEqual(engine._probe_duration_seconds(str(path)), 30.0)
                self.assertEqual(engine._audio_format_from_path(str(path)), AudioFormat.ALAC)
                self.assertEqual(
                    engine._quality_label_from_file(
                        str(path),
                        AudioFormat.AAC,
                        "fallback",
                    ),
                    "ALAC 24-bit/96kHz",
                )
                self.assertFalse(engine._should_convert_output(str(path), "source"))
                self.assertTrue(engine._should_convert_output(str(path), "alac-16"))
                self.assertEqual(mutagen.call_count, 1)
                self.assertEqual(ffprobe.call_count, 1)
                self.assertEqual(path.read_bytes(), initial_bytes)

                path.write_bytes(initial_bytes + b"+")
                engine._probe_audio(str(path))
                self.assertEqual(mutagen.call_count, 2)
                self.assertEqual(ffprobe.call_count, 2)

                stat = path.stat()
                os.utime(
                    path,
                    ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
                )
                engine._probe_audio(str(path))
                self.assertEqual(mutagen.call_count, 3)
                self.assertEqual(ffprobe.call_count, 3)

                copied = Path(tmp) / "renamed.m4a"
                shutil.copy2(path, copied)
                engine._probe_audio(str(copied))
                self.assertEqual(mutagen.call_count, 4)
                self.assertEqual(ffprobe.call_count, 4)

    def test_concurrent_probe_failure_is_cached_and_singleflight(self):
        engine = self.make_engine()
        started = threading.Event()
        release = threading.Event()

        def failing_mutagen(_path):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            raise ValueError("invalid audio fixture")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.flac"
            path.write_bytes(_AUDIO_BYTES)
            with (
                patch("antra.core.engine.MutagenFile", side_effect=failing_mutagen) as mutagen,
                ThreadPoolExecutor(max_workers=8) as pool,
            ):
                futures = [pool.submit(engine._probe_audio, str(path)) for _ in range(8)]
                self.assertTrue(started.wait(timeout=1))
                release.set()
                probes = [future.result(timeout=2) for future in futures]
                self.assertEqual(mutagen.call_count, 1)
                self.assertTrue(all(probe is probes[0] for probe in probes))
                self.assertIsNone(engine._probe_duration_seconds(str(path)))
                self.assertEqual(mutagen.call_count, 1)

    def test_concurrent_decode_failure_runs_ffmpeg_probe_once(self):
        engine = self.make_engine()
        started = threading.Event()
        release = threading.Event()

        def failed_decode(_path):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.flac"
            path.write_bytes(_AUDIO_BYTES)
            with (
                patch.object(
                    engine,
                    "_run_flac_decode_probe",
                    side_effect=failed_decode,
                ) as ffmpeg,
                ThreadPoolExecutor(max_workers=6) as pool,
            ):
                futures = [
                    pool.submit(engine._fails_flac_decode_probe, str(path))
                    for _ in range(6)
                ]
                self.assertTrue(started.wait(timeout=1))
                release.set()
                self.assertEqual(
                    [future.result(timeout=2) for future in futures],
                    [True] * 6,
                )
                self.assertTrue(engine._fails_flac_decode_probe(str(path)))
                self.assertEqual(ffmpeg.call_count, 1)


if __name__ == "__main__":
    unittest.main()
