import json
import unittest
from unittest.mock import MagicMock, patch

from antra.utils import perf


class PerfMeasurementTests(unittest.TestCase):
    def test_disabled_helpers_do_not_start_clock_or_inspect_payload(self):
        class ExplosivePayload:
            def __str__(self):
                raise AssertionError("disabled payload measurement did work")

        logger = MagicMock()
        with patch.object(perf, "PERF_ENABLED", False):
            self.assertIsNone(perf.start_phase())
            self.assertIsNone(
                perf.log_payload(logger, "disabled", ExplosivePayload())
            )
        logger.info.assert_not_called()

    def test_phase_log_uses_monotonic_clock_and_existing_timing_marker(self):
        logger = MagicMock()
        with (
            patch.object(perf, "PERF_ENABLED", True),
            patch.object(
                perf.time,
                "perf_counter_ns",
                side_effect=[1_000_000_000, 3_500_000_000],
            ),
        ):
            started = perf.start_phase()
            elapsed = perf.log_phase(
                logger,
                "resolve",
                started,
                subject="Fixture Track",
                counts={"adapters": 3},
            )

        self.assertEqual(elapsed, 2.5)
        logger.info.assert_called_once_with(
            "  [TIMING]  %s %.2fs%s",
            "Fixture Track resolve",
            2.5,
            " adapters=3",
        )

    def test_payload_size_is_utf8_wire_size(self):
        text = '{"title":"Beyoncé"}'
        self.assertEqual(perf.payload_size_bytes(text), len(text.encode("utf-8")))
        self.assertEqual(perf.payload_size_bytes(text.encode("utf-8")), len(text.encode("utf-8")))

        payload = {"title": "Beyoncé", "tracks": [1, 2]}
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self.assertEqual(perf.payload_size_bytes(payload), len(encoded))


if __name__ == "__main__":
    unittest.main()
