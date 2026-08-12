import unittest

from antra.core.models import AudioFormat, SearchResult, TrackMetadata
from antra.core.resolver import SourceResolver
from antra.sources.base import BaseSourceAdapter


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
    bit_depth: int,
    sample_rate_hz: int,
    similarity_score: float = 0.95,
    duration_ms: int = 180_000,
    isrc_match: bool = False,
) -> SearchResult:
    return SearchResult(
        source=source,
        title="Fixture Song",
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
    )


class FixtureAdapter(BaseSourceAdapter):
    def __init__(self, name: str, result: SearchResult, priority: int = 1):
        self.name = name
        self.priority = priority
        self.result = result
        self.search_calls = 0

    def is_available(self):
        return True

    def search(self, track):
        self.search_calls += 1
        return self.result

    def download(self, result, output_path):
        raise AssertionError("quality guardrails must never download")


class ResolverQualityGuardrailTests(unittest.TestCase):
    def test_lossless_winner_preserves_adapter_bit_depth_and_sample_rate(self):
        adapters = [
            FixtureAdapter("cd", make_result("cd", bit_depth=16, sample_rate_hz=44_100)),
            FixtureAdapter("hires-96", make_result("hires-96", bit_depth=24, sample_rate_hz=96_000)),
            FixtureAdapter("hires-192", make_result("hires-192", bit_depth=24, sample_rate_hz=192_000)),
        ]
        resolver = SourceResolver(adapters, preferred_output_format="lossless")

        result, adapter = resolver.resolve(make_track())

        self.assertEqual(adapter.name, "hires-192")
        self.assertEqual(result.source, "hires-192")
        self.assertEqual((result.bit_depth, result.sample_rate_hz), (24, 192_000))
        self.assertEqual([adapter.search_calls for adapter in adapters], [1, 1, 1])

    def test_lossless_16_prefers_exact_depth_over_higher_depth(self):
        adapters = [
            FixtureAdapter("studio", make_result("studio", bit_depth=24, sample_rate_hz=192_000)),
            FixtureAdapter("compact-disc", make_result("compact-disc", bit_depth=16, sample_rate_hz=44_100)),
        ]
        resolver = SourceResolver(adapters, preferred_output_format="lossless-16")

        result, adapter = resolver.resolve(make_track())

        self.assertEqual(adapter.name, "compact-disc")
        self.assertEqual((result.bit_depth, result.sample_rate_hz), (16, 44_100))

    def test_strict_matching_rejects_near_match_and_keeps_exact_winner(self):
        adapters = [
            FixtureAdapter(
                "near-match",
                make_result(
                    "near-match",
                    bit_depth=24,
                    sample_rate_hz=192_000,
                    similarity_score=0.87,
                ),
            ),
            FixtureAdapter(
                "strict-match",
                make_result(
                    "strict-match",
                    bit_depth=24,
                    sample_rate_hz=96_000,
                    similarity_score=0.90,
                ),
            ),
        ]
        resolver = SourceResolver(
            adapters,
            preferred_output_format="lossless",
            strict_matching=True,
        )

        result, adapter = resolver.resolve(make_track())

        self.assertEqual(adapter.name, "strict-match")
        self.assertEqual(result.source, "strict-match")
        self.assertEqual((result.bit_depth, result.sample_rate_hz), (24, 96_000))

    def test_strict_matching_rejects_duration_mismatch_even_with_isrc(self):
        mismatched = FixtureAdapter(
            "duration-mismatch",
            make_result(
                "duration-mismatch",
                bit_depth=24,
                sample_rate_hz=192_000,
                similarity_score=0.99,
                duration_ms=220_000,
                isrc_match=True,
            ),
        )
        track = make_track(duration_ms=180_000)

        strict = SourceResolver(
            [mismatched],
            preferred_output_format="lossless",
            strict_matching=True,
        )
        permissive = SourceResolver(
            [mismatched],
            preferred_output_format="lossless",
            strict_matching=False,
        )

        self.assertIsNone(strict.resolve(track))
        result, adapter = permissive.resolve(track)
        self.assertIs(result, mismatched.result)
        self.assertIs(adapter, mismatched)

    def test_quality_winner_is_stable_across_rotating_tiers(self):
        adapters = [
            FixtureAdapter("a-16", make_result("a-16", bit_depth=16, sample_rate_hz=48_000)),
            FixtureAdapter("b-96", make_result("b-96", bit_depth=24, sample_rate_hz=96_000)),
            FixtureAdapter("c-192", make_result("c-192", bit_depth=24, sample_rate_hz=192_000)),
        ]
        resolver = SourceResolver(adapters, preferred_output_format="lossless")

        winners = []
        for _ in range(250):
            result, adapter = resolver.resolve(make_track())
            winners.append(
                (adapter.name, result.source, result.bit_depth, result.sample_rate_hz)
            )

        self.assertEqual(
            set(winners),
            {("c-192", "c-192", 24, 192_000)},
        )


if __name__ == "__main__":
    unittest.main()
