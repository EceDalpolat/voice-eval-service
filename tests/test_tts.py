from django.test import SimpleTestCase

from evaluations.services.providers import normalize_tts
from evaluations.services.tts import evaluate_tts
from evaluations.services.types import score_component

from . import audio_factory as af
from .helpers import failed_rules, rules

TEXT = "Siparişiniz yarın teslim edilecek."  # 34 chars -> ~2.6 s at 13 cps


def run(tts, audio=None, provider=None):
    return score_component(evaluate_tts(tts, rules("tr", tts_provider=provider), tts_audio=audio), 0.5)


def tts(**kw):
    base = {"provider": "generic", "text": TEXT, "duration_ms": 2600, "latency_ms": 500, "attempt": 1}
    base.update(kw)
    return base


class ProviderAdapterTests(SimpleTestCase):
    def test_generic(self):
        n = normalize_tts(tts())
        self.assertEqual((n.duration_ms, n.latency_ms), (2600, 500))

    def test_seconds_vendor_converts_units(self):
        n = normalize_tts({"provider": "seconds_vendor_example", "text": TEXT,
                           "provider_metadata": {"audio_length_seconds": 2.4, "time_to_first_byte_ms": 300}})
        self.assertEqual((n.duration_ms, n.latency_ms), (2400, 300))

    def test_nested_vendor(self):
        n = normalize_tts({"provider": "nested_vendor_example", "text": TEXT,
                           "provider_metadata": {"audio": {"durationMs": 2500, "sampleRateHz": 24000},
                                                 "processingMs": 700}})
        self.assertEqual((n.duration_ms, n.sample_rate, n.latency_ms), (2500, 24000, 700))

    def test_unknown_provider_uses_generic(self):
        self.assertEqual(normalize_tts(tts(provider="brand_new")).duration_ms, 2600)


class TTSEvaluationTests(SimpleTestCase):
    def test_good_output_passes(self):
        result = run(tts())
        self.assertFalse(result.failed)

    def test_empty_audio(self):
        self.assertIn("tts.empty_audio", failed_rules(run(tts(duration_ms=0))))

    def test_truncated_audio(self):
        result = run(tts(duration_ms=500))
        self.assertTrue(result.failed)
        self.assertIn("tts.duration_anomaly", failed_rules(result))

    def test_too_long_audio(self):
        self.assertIn("tts.duration_anomaly", failed_rules(run(tts(duration_ms=12000))))

    def test_latency_levels(self):
        warn = run(tts(latency_ms=2000))
        self.assertIn("tts.high_latency", failed_rules(warn))
        self.assertFalse(warn.failed)
        critical = run(tts(latency_ms=5000))
        self.assertTrue(critical.failed)

    def test_provider_specific_latency(self):
        self.assertNotIn("tts.high_latency", failed_rules(run(tts(latency_ms=2000), provider="slow_tts_example")))

    def test_generated_audio_is_checked(self):
        result = run(tts(duration_ms=None), audio=af.silence(seconds=2.6))
        self.assertIn("tts.audio.mostly_silent", failed_rules(result))
        self.assertEqual(result.signals["normalized"]["duration_ms"], 2600.0)
