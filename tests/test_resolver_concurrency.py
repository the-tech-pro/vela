import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from antra.core.models import AudioFormat, SearchResult, TrackMetadata
from antra.core.resolver import SourceResolver
from antra.sources.base import BaseSourceAdapter, RateLimitedError


def make_track(**overrides) -> TrackMetadata:
    values = {
        "title": "Fixture Song",
        "artists": ["Fixture Artist"],
        "album": "Fixture Album",
        "duration_ms": 180_000,
        "isrc": "GBVEL2600001",
    }
    values.update(overrides)
    return TrackMetadata(**values)


def make_result(
    source: str,
    *,
    bit_depth: int = 24,
    sample_rate_hz: int = 96_000,
    similarity_score: float = 0.95,
    duration_ms: int = 180_000,
    isrc_match: bool = False,
    title: str = "Fixture Song",
    is_explicit=None,
) -> SearchResult:
    return SearchResult(
        source=source,
        title=title,
        artists=["Fixture Artist"],
        album="Fixture Album",
        duration_ms=duration_ms,
        audio_format=AudioFormat.FLAC,
        quality_kbps=None,
        is_lossless=True,
        download_url=f"fixture://{source}",
        stream_id=source,
        similarity_score=similarity_score,
        isrc_match=isrc_match,
        bit_depth=bit_depth,
        sample_rate_hz=sample_rate_hz,
        is_explicit=is_explicit,
    )


class ConcurrencyTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started_at = {}
        self.finished_at = {}

    def enter(self, name: str) -> None:
        with self._lock:
            self.started_at[name] = time.perf_counter()
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self, name: str) -> None:
        with self._lock:
            self.active -= 1
            self.finished_at[name] = time.perf_counter()


class FixtureAdapter(BaseSourceAdapter):
    def __init__(
        self,
        name: str,
        *,
        result: SearchResult = None,
        priority: int = 1,
        delay: float = 0.0,
        error_type=None,
        tracker: ConcurrencyTracker = None,
        max_concurrent_searches: int = 8,
    ):
        self.name = name
        self.priority = priority
        self.result = result
        self.delay = delay
        self.error_type = error_type
        self.tracker = tracker
        self.max_concurrent_searches = max_concurrent_searches
        self.search_calls = 0
        self._calls_lock = threading.Lock()

    def is_available(self):
        return True

    def search(self, track):
        with self._calls_lock:
            self.search_calls += 1
        if self.tracker:
            self.tracker.enter(self.name)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error_type:
                raise self.error_type(f"{self.name} failure")
            if callable(self.result):
                return self.result(track)
            return self.result
        finally:
            if self.tracker:
                self.tracker.exit(self.name)

    def download(self, result, output_path):
        raise AssertionError("resolver concurrency tests must never download")


class SerialReferenceResolver(SourceResolver):
    """Run the production batching/selection path with serial adapter searches."""

    def _search_adapter_batch(
        self,
        adapters,
        track,
        mark_rate_limits=True,
    ):
        outcomes = []
        for adapter in adapters:
            result, error = self._capture_search_outcome(
                adapter,
                track,
                mark_rate_limits,
            )
            outcomes.append((adapter, result, error))
        return outcomes


class FixedProviderStats:
    def __init__(self, order):
        self.order = {name: index for index, name in enumerate(order)}

    def rank(self, adapters):
        return sorted(
            adapters,
            key=lambda adapter: self.order.get(adapter.name, len(self.order)),
        )


def resolution_signature(resolution):
    if resolution is None:
        return None
    result, adapter = resolution
    return (
        adapter.name,
        result.source,
        result.bit_depth,
        result.sample_rate_hz,
    )


class ResolverConcurrencyTests(unittest.TestCase):
    def test_rotation_order_not_completion_order_breaks_quality_ties(self):
        parallel_adapters = [
            FixtureAdapter(
                "a-slow",
                result=make_result("a-slow"),
                delay=0.035,
            ),
            FixtureAdapter(
                "b-fast",
                result=make_result("b-fast"),
                delay=0.001,
            ),
            FixtureAdapter(
                "c-medium",
                result=make_result("c-medium"),
                delay=0.015,
            ),
        ]
        serial_adapters = [
            FixtureAdapter(
                adapter.name,
                result=make_result(adapter.name),
                delay=adapter.delay,
            )
            for adapter in parallel_adapters
        ]
        parallel = SourceResolver(
            parallel_adapters,
            preferred_output_format="lossless",
        )
        serial = SerialReferenceResolver(
            serial_adapters,
            preferred_output_format="lossless",
        )

        parallel_winners = []
        serial_winners = []
        for _ in range(6):
            parallel_winners.append(
                resolution_signature(parallel.resolve(make_track()))
            )
            serial_winners.append(
                resolution_signature(serial.resolve(make_track()))
            )

        self.assertEqual(parallel_winners, serial_winners)
        self.assertEqual(
            [winner[0] for winner in parallel_winners[:3]],
            ["a-slow", "b-fast", "c-medium"],
        )

    def test_provider_stats_order_remains_tie_breaker(self):
        stats = FixedProviderStats(["preferred-slow", "fast"])
        parallel = SourceResolver(
            [
                FixtureAdapter(
                    "fast",
                    result=make_result("fast"),
                    delay=0.001,
                ),
                FixtureAdapter(
                    "preferred-slow",
                    result=make_result("preferred-slow"),
                    delay=0.035,
                ),
            ],
            preferred_output_format="lossless",
            provider_stats=stats,
        )
        serial = SerialReferenceResolver(
            [
                FixtureAdapter(
                    "fast",
                    result=make_result("fast"),
                    delay=0.001,
                ),
                FixtureAdapter(
                    "preferred-slow",
                    result=make_result("preferred-slow"),
                    delay=0.035,
                ),
            ],
            preferred_output_format="lossless",
            provider_stats=stats,
        )

        parallel_result = resolution_signature(parallel.resolve(make_track()))
        serial_result = resolution_signature(serial.resolve(make_track()))

        self.assertEqual(parallel_result, serial_result)
        self.assertEqual(parallel_result[0], "preferred-slow")

    def test_parallel_and_serial_decisions_match_for_failures_and_filters(self):
        scenarios = {
            "search-error": {
                "track": make_track(),
                "strict": False,
                "adapters": [
                    {
                        "name": "broken",
                        "error_type": RuntimeError,
                        "delay": 0.001,
                    },
                    {
                        "name": "winner",
                        "result": make_result(
                            "winner",
                            bit_depth=24,
                            sample_rate_hz=96_000,
                        ),
                        "delay": 0.02,
                    },
                ],
            },
            "strict-identity": {
                "track": make_track(),
                "strict": True,
                "adapters": [
                    {
                        "name": "near-highres",
                        "result": make_result(
                            "near-highres",
                            bit_depth=24,
                            sample_rate_hz=192_000,
                            similarity_score=0.87,
                        ),
                        "delay": 0.001,
                    },
                    {
                        "name": "strict-winner",
                        "result": make_result(
                            "strict-winner",
                            bit_depth=24,
                            sample_rate_hz=96_000,
                            similarity_score=0.90,
                        ),
                        "delay": 0.02,
                    },
                ],
            },
            "explicit-preference": {
                "track": make_track(is_explicit=True),
                "strict": False,
                "adapters": [
                    {
                        "name": "clean-highres",
                        "result": make_result(
                            "clean-highres",
                            bit_depth=24,
                            sample_rate_hz=192_000,
                            similarity_score=0.99,
                            title="Fixture Song (Clean Version)",
                            is_explicit=False,
                        ),
                        "delay": 0.001,
                    },
                    {
                        "name": "explicit-winner",
                        "result": make_result(
                            "explicit-winner",
                            bit_depth=24,
                            sample_rate_hz=96_000,
                            similarity_score=0.90,
                            is_explicit=True,
                        ),
                        "delay": 0.02,
                    },
                ],
            },
        }

        for name, scenario in scenarios.items():
            with self.subTest(name=name):
                parallel_adapters = [
                    FixtureAdapter(**adapter)
                    for adapter in scenario["adapters"]
                ]
                serial_adapters = [
                    FixtureAdapter(**adapter)
                    for adapter in scenario["adapters"]
                ]
                parallel = SourceResolver(
                    parallel_adapters,
                    preferred_output_format="lossless",
                    strict_matching=scenario["strict"],
                )
                serial = SerialReferenceResolver(
                    serial_adapters,
                    preferred_output_format="lossless",
                    strict_matching=scenario["strict"],
                )

                parallel_resolution = parallel.resolve(scenario["track"])
                serial_resolution = serial.resolve(scenario["track"])

                self.assertEqual(
                    resolution_signature(parallel_resolution),
                    resolution_signature(serial_resolution),
                )
                self.assertEqual(
                    parallel.last_resolve_report(),
                    serial.last_resolve_report(),
                )

    def test_rate_limit_cooldown_and_report_match_serial_waterfall(self):
        def adapters():
            return [
                FixtureAdapter(
                    "rate-limited",
                    priority=1,
                    error_type=RateLimitedError,
                ),
                FixtureAdapter(
                    "fallback",
                    priority=2,
                    result=make_result("fallback", isrc_match=True),
                ),
            ]

        parallel_adapters = adapters()
        serial_adapters = adapters()
        parallel = SourceResolver(
            parallel_adapters,
            preferred_output_format="ogg",
        )
        serial = SerialReferenceResolver(
            serial_adapters,
            preferred_output_format="ogg",
        )

        for _ in range(2):
            self.assertEqual(
                resolution_signature(parallel.resolve(make_track())),
                resolution_signature(serial.resolve(make_track())),
            )
            self.assertEqual(
                parallel.last_resolve_report(),
                serial.last_resolve_report(),
            )

        self.assertTrue(parallel._is_rate_limited("rate-limited"))
        self.assertTrue(serial._is_rate_limited("rate-limited"))
        self.assertEqual(parallel_adapters[0].search_calls, 1)
        self.assertEqual(serial_adapters[0].search_calls, 1)
        self.assertEqual(
            parallel.last_resolve_report(),
            {"fallback": "found (FLAC 24-bit/96kHz)"},
        )

    def test_parallel_rate_limit_marks_cooldown_without_waiting_for_slow_peer(self):
        release_slow_search = threading.Event()

        def slow_result(track):
            release_slow_search.wait(timeout=1)
            return make_result("a-slow")

        resolver = SourceResolver(
            [
                FixtureAdapter(
                    "a-slow",
                    result=slow_result,
                ),
                FixtureAdapter(
                    "b-rate-limited",
                    error_type=RateLimitedError,
                ),
            ],
            preferred_output_format="lossless",
        )
        cooldown_marked = threading.Event()
        mark_rate_limited = resolver._mark_rate_limited

        def mark_and_signal(adapter_name, cooldown_seconds=None):
            mark_rate_limited(adapter_name, cooldown_seconds)
            cooldown_marked.set()

        resolver._mark_rate_limited = mark_and_signal

        def resolve_with_report():
            result = resolver.resolve(make_track())
            return result, resolver.last_resolve_report()

        with ThreadPoolExecutor(max_workers=1) as executor:
            resolution = executor.submit(resolve_with_report)
            self.assertTrue(cooldown_marked.wait(timeout=0.5))
            self.assertFalse(resolution.done())
            release_slow_search.set()
            result, report = resolution.result()
            self.assertEqual(
                resolution_signature(result)[0],
                "a-slow",
            )
            self.assertEqual(
                report,
                {
                    "a-slow": "found (FLAC 24-bit/96kHz)",
                    "b-rate-limited": "rate-limited",
                },
            )

        self.assertTrue(resolver._is_rate_limited("b-rate-limited"))
        self.assertEqual(
            resolver.last_resolve_report(),
            {},
        )

    def test_legacy_waterfalls_do_not_start_unneeded_searches(self):
        first = FixtureAdapter(
            "a-first",
            result=make_result("a-first", isrc_match=True),
        )
        later = FixtureAdapter(
            "b-later",
            result=make_result("b-later", isrc_match=True),
        )
        resolver = SourceResolver(
            [first, later],
            preferred_output_format="ogg",
        )

        result = resolver.resolve(make_track())

        self.assertEqual(resolution_signature(result)[0], "a-first")
        self.assertEqual(first.search_calls, 1)
        self.assertEqual(later.search_calls, 0)

    def test_lossless_16_deezer_early_acceptance_remains_serial(self):
        deezer = FixtureAdapter(
            "deezer",
            result=make_result(
                "deezer",
                bit_depth=16,
                sample_rate_hz=44_100,
            ),
        )
        later = FixtureAdapter(
            "z-later",
            result=make_result(
                "z-later",
                bit_depth=24,
                sample_rate_hz=192_000,
            ),
        )
        resolver = SourceResolver(
            [deezer, later],
            preferred_output_format="lossless-16",
        )

        result = resolver.resolve(make_track())

        self.assertEqual(resolution_signature(result)[0], "deezer")
        self.assertEqual(deezer.search_calls, 1)
        self.assertEqual(later.search_calls, 0)

    def test_same_tier_latency_tracks_slowest_search_instead_of_sum(self):
        delay = 0.12

        def adapters():
            return [
                FixtureAdapter(
                    name,
                    result=make_result(
                        name,
                        sample_rate_hz=sample_rate,
                    ),
                    delay=delay,
                )
                for name, sample_rate in [
                    ("a", 48_000),
                    ("b", 96_000),
                    ("c", 192_000),
                ]
            ]

        parallel = SourceResolver(
            adapters(),
            preferred_output_format="lossless",
        )
        serial = SerialReferenceResolver(
            adapters(),
            preferred_output_format="lossless",
        )

        started = time.perf_counter()
        parallel_result = parallel.resolve(make_track())
        parallel_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        serial_result = serial.resolve(make_track())
        serial_elapsed = time.perf_counter() - started

        self.assertEqual(
            resolution_signature(parallel_result),
            resolution_signature(serial_result),
        )
        self.assertLess(parallel_elapsed, serial_elapsed * 0.75)
        self.assertGreater(serial_elapsed, delay * 2.5)

    def test_priority_tiers_do_not_overlap(self):
        tracker = ConcurrencyTracker()
        adapters = [
            FixtureAdapter(
                "tier-one-a",
                result=make_result("tier-one-a"),
                priority=1,
                delay=0.05,
                tracker=tracker,
            ),
            FixtureAdapter(
                "tier-one-b",
                result=make_result("tier-one-b"),
                priority=1,
                delay=0.05,
                tracker=tracker,
            ),
            FixtureAdapter(
                "tier-two",
                result=make_result("tier-two"),
                priority=2,
                delay=0.01,
                tracker=tracker,
            ),
        ]
        resolver = SourceResolver(
            adapters,
            preferred_output_format="lossless",
        )

        resolver.resolve(make_track())

        first_tier_finished = max(
            tracker.finished_at["tier-one-a"],
            tracker.finished_at["tier-one-b"],
        )
        self.assertGreaterEqual(
            tracker.started_at["tier-two"],
            first_tier_finished,
        )

    def test_global_executor_and_provider_semaphore_bound_concurrency(self):
        global_tracker = ConcurrencyTracker()
        adapters = [
            FixtureAdapter(
                f"adapter-{index:02}",
                result=make_result(f"adapter-{index:02}"),
                delay=0.04,
                tracker=global_tracker,
            )
            for index in range(SourceResolver.MAX_PARALLEL_SEARCHES + 4)
        ]
        resolver = SourceResolver(
            adapters,
            preferred_output_format="lossless",
        )

        resolver.resolve(make_track())

        self.assertGreater(global_tracker.max_active, 1)
        self.assertLessEqual(
            global_tracker.max_active,
            SourceResolver.MAX_PARALLEL_SEARCHES,
        )

        provider_tracker = ConcurrencyTracker()

        def result_for_track(track):
            index = int(track.title.rsplit(" ", 1)[-1])
            return make_result("limited") if index % 2 else None

        provider = FixtureAdapter(
            "limited",
            result=result_for_track,
            delay=0.04,
            tracker=provider_tracker,
            max_concurrent_searches=2,
        )
        limited_resolver = SourceResolver(
            [provider],
            preferred_output_format="lossless",
        )

        def resolve_and_report(index):
            resolution = limited_resolver.resolve(
                make_track(title=f"Fixture Song {index}")
            )
            return (
                resolution_signature(resolution),
                limited_resolver.last_resolve_report(),
            )

        with ThreadPoolExecutor(max_workers=6) as executor:
            outcomes = list(executor.map(resolve_and_report, range(6)))

        self.assertEqual(provider_tracker.max_active, 2)
        self.assertEqual(provider.search_calls, 6)
        for index, (signature, report) in enumerate(outcomes):
            if index % 2:
                self.assertEqual(signature[0], "limited")
                self.assertEqual(
                    report,
                    {"limited": "found (FLAC 24-bit/96kHz)"},
                )
            else:
                self.assertIsNone(signature)
                self.assertEqual(
                    report,
                    {"limited": "no catalog match"},
                )


if __name__ == "__main__":
    unittest.main()
