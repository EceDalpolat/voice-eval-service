from django.test import SimpleTestCase

from evaluations.services.stt import evaluate_stt, tokenize
from evaluations.services.types import score_component

from .helpers import failed_rules, rules


def run(stt, language="tr", audio_ms=None, provider=None):
    return score_component(evaluate_stt(stt, rules(language, stt_provider=provider), audio_ms), 0.5)


def stt(text="siparişim nerede acaba", confidence=0.92, latency=400, **kw):
    return {"provider": kw.pop("provider", "demo_stt"), "transcript": text, "confidence": confidence,
            "latency_ms": latency, **kw}


class STTEvaluationTests(SimpleTestCase):
    def test_good_transcript_passes(self):
        result = run(stt())
        self.assertFalse(result.failed)
        self.assertEqual(result.score, 1.0)

    def test_empty_transcript(self):
        result = run(stt(text="   "))
        self.assertTrue(result.failed)
        self.assertIn("stt.empty_transcript", failed_rules(result))

    def test_low_confidence(self):
        result = run(stt(confidence=0.42))
        self.assertTrue(result.failed)
        self.assertIn("stt.low_confidence", failed_rules(result))

    def test_missing_confidence_is_not_penalised(self):
        self.assertFalse(run(stt(confidence=None)).failed)

    def test_provider_specific_threshold(self):
        self.assertFalse(run(stt(confidence=0.5), provider="lenient_confidence_example").failed)
        self.assertTrue(run(stt(confidence=0.5)).failed)

    def test_turkish_filler_only(self):
        self.assertIn("stt.filler_only", failed_rules(run(stt(text="ııı şey"))))

    def test_english_filler_only(self):
        self.assertIn("stt.filler_only", failed_rules(run(stt(text="um uh"), language="en")))

    def test_short_real_answer_is_fine(self):
        # "evet" is a valid call-center answer; short != bad.
        self.assertFalse(run(stt(text="evet")).failed)

    def test_turkish_characters_are_not_garbage(self):
        result = run(stt(text="Şöyle ki ğüıöç İstanbul'da"))
        self.assertNotIn("stt.garbage_characters", failed_rules(result))

    def test_garbage_characters(self):
        self.assertIn("stt.garbage_characters", failed_rules(run(stt(text="��� ### @@@ ???"))))

    def test_wrong_script_for_language(self):
        self.assertIn("stt.garbage_characters", failed_rules(run(stt(text="Привет как дела"))))

    def test_repeated_tokens_warning(self):
        result = run(stt(text="tamam tamam tamam tamam tamam"))
        self.assertIn("stt.repeated_tokens", failed_rules(result))

    def test_speech_rate_uses_audio_duration(self):
        result = run(stt(text="evet"), audio_ms=8000)
        self.assertIn("stt.speech_rate", failed_rules(result))

    def test_high_latency_is_a_warning(self):
        result = run(stt(latency=2500))
        self.assertIn("stt.high_latency", failed_rules(result))
        self.assertFalse(result.failed)

    def test_turkish_dotted_i_lowercasing(self):
        self.assertEqual(tokenize("IŞIK İzmir", "tr"), ["ışık", "izmir"])
