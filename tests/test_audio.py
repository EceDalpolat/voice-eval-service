from django.test import SimpleTestCase

from evaluations.services.audio import evaluate_audio
from evaluations.services.types import score_component

from . import audio_factory as af
from .helpers import failed_rules, rules


def run(data):
    return score_component(evaluate_audio(data, rules()), 0.5)


class AudioEvaluationTests(SimpleTestCase):
    def test_no_audio_is_skipped(self):
        result = run(None)
        self.assertFalse(result.evaluated)
        self.assertIsNone(result.score)

    def test_clean_speech_passes(self):
        result = run(af.clean_speech())
        self.assertFalse(result.failed)
        self.assertEqual(result.score, 1.0)

    def test_stereo_is_mixed_to_mono(self):
        self.assertFalse(run(af.stereo_clean()).failed)

    def test_silence_fails(self):
        result = run(af.silence())
        self.assertTrue(result.failed)
        self.assertIn("audio.mostly_silent", failed_rules(result))

    def test_quiet_audio_fails(self):
        self.assertIn("audio.too_quiet", failed_rules(run(af.too_quiet())))

    def test_clipping_detected(self):
        result = run(af.clipped())
        self.assertTrue(result.failed)
        self.assertIn("audio.clipping", failed_rules(result))

    def test_noise_is_only_a_warning(self):
        result = run(af.noisy())
        self.assertIn("audio.low_snr", failed_rules(result))
        self.assertFalse(result.failed)
        self.assertLess(result.score, 1.0)

    def test_too_short(self):
        self.assertIn("audio.too_short", failed_rules(run(af.too_short())))

    def test_unreadable_audio_is_a_critical_finding(self):
        result = run(af.not_a_wav())
        self.assertTrue(result.failed)
        self.assertEqual(failed_rules(result), {"audio.unreadable"})

    def test_disabled_check_is_not_run(self):
        r = rules()
        r["audio"]["checks"]["clipping"]["enabled"] = False
        result = score_component(evaluate_audio(af.clipped(), r), 0.5)
        self.assertNotIn("audio.clipping", {f.rule for f in result.findings})
