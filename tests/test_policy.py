from django.test import SimpleTestCase

from evaluations.services.policy import PolicyContext, decide

from .helpers import component, rules

POLICY = rules()["policy"]


def ctx(audio=None, stt=None, tts=None, attempts=None, prior=0):
    return PolicyContext(
        components={
            "audio": audio or component("audio"),
            "stt": stt or component("stt"),
            "tts": tts or component("tts"),
        },
        attempts=attempts or {"stt": 1, "tts": 1},
        prior_failures=prior,
    )


class PolicyTests(SimpleTestCase):
    def test_all_good_accepts(self):
        d = decide(ctx(), POLICY)
        self.assertEqual((d.action, d.policy), ("accept", "default"))

    def test_bad_audio_asks_repeat_even_if_stt_failed(self):
        d = decide(ctx(audio=component("audio", True, ["audio.too_quiet"]),
                       stt=component("stt", True, ["stt.low_confidence"])), POLICY)
        self.assertEqual(d.action, "ask_repeat")
        self.assertEqual(d.policy, "unusable_caller_audio")
        self.assertEqual([f["rule"] for f in d.triggered_by], ["audio.too_quiet"])

    def test_stt_failure_on_good_audio_retries_stt(self):
        d = decide(ctx(stt=component("stt", True, ["stt.low_confidence"])), POLICY)
        self.assertEqual(d.action, "retry_stt")

    def test_filler_only_asks_repeat(self):
        d = decide(ctx(stt=component("stt", True, ["stt.filler_only"])), POLICY)
        self.assertEqual(d.action, "ask_repeat")

    def test_second_stt_attempt_switches_provider(self):
        d = decide(ctx(stt=component("stt", True, ["stt.low_confidence"]), attempts={"stt": 2, "tts": 1}), POLICY)
        self.assertEqual((d.action, d.target), ("switch_provider", "stt"))

    def test_stt_exhausted_asks_repeat(self):
        d = decide(ctx(stt=component("stt", True, ["stt.low_confidence"]), attempts={"stt": 3, "tts": 1}), POLICY)
        self.assertEqual(d.action, "ask_repeat")

    def test_stt_has_priority_over_tts(self):
        d = decide(ctx(stt=component("stt", True, ["stt.empty_transcript"]),
                       tts=component("tts", True, ["tts.empty_audio"])), POLICY)
        self.assertEqual(d.action, "retry_stt")

    def test_tts_failure_retries_tts(self):
        self.assertEqual(decide(ctx(tts=component("tts", True, ["tts.empty_audio"])), POLICY).action, "retry_tts")

    def test_tts_switch_and_fallback(self):
        bad = component("tts", True, ["tts.empty_audio"])
        d2 = decide(ctx(tts=bad, attempts={"stt": 1, "tts": 2}), POLICY)
        self.assertEqual((d2.action, d2.target), ("switch_provider", "tts"))
        self.assertEqual(decide(ctx(tts=bad, attempts={"stt": 1, "tts": 3}), POLICY).action, "safe_fallback")

    def test_repeated_failures_in_call_fall_back(self):
        d = decide(ctx(prior=3), POLICY)
        self.assertEqual((d.action, d.policy), ("safe_fallback", "repeated_failures_in_call"))

    def test_decision_is_explained_with_trace(self):
        d = decide(ctx(tts=component("tts", True, ["tts.empty_audio"])), POLICY)
        self.assertIn("tts.empty_audio failed", d.explanation)
        self.assertEqual(d.trace[-1]["policy"], "tts_failed")
        self.assertTrue(d.trace[-1]["matched"])
        self.assertTrue(all(not t["matched"] for t in d.trace[:-1]))

    def test_warnings_are_listed_on_accept(self):
        d = decide(ctx(stt=component("stt", False, ["stt.high_latency"], severity="warning")), POLICY)
        self.assertEqual(d.action, "accept")
        self.assertIn("Non-blocking warnings", d.explanation)
