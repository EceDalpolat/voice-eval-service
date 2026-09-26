from django.test import SimpleTestCase

from evaluations.services.rules import RuleConfigError, deep_merge, load_ruleset_from_text, normalize_language

from .helpers import RULES_PATH, rules


class RuleResolutionTests(SimpleTestCase):
    def test_deep_merge_overrides_nested_values_only(self):
        merged = deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}})
        self.assertEqual(merged, {"a": {"b": 1, "c": 3}})

    def test_language_normalisation(self):
        self.assertEqual(normalize_language("tr-TR"), "tr")
        self.assertEqual(normalize_language("EN_us"), "en")

    def test_language_override_applies(self):
        self.assertEqual(rules("tr")["stt"]["checks"]["low_confidence"]["min_confidence"], 0.6)
        self.assertEqual(rules("en")["stt"]["checks"]["low_confidence"]["min_confidence"], 0.65)

    def test_unknown_language_falls_back_to_default(self):
        r = rules("de")
        self.assertEqual(r["language"], "en")
        self.assertIn("um", r["lexicon"]["filler_words"])

    def test_provider_override_applies_only_to_that_provider(self):
        self.assertEqual(
            rules("tr", stt_provider="lenient_confidence_example")["stt"]["checks"]["low_confidence"]["min_confidence"], 0.45)
        self.assertEqual(rules("tr", stt_provider="other")["stt"]["checks"]["low_confidence"]["min_confidence"], 0.6)
        self.assertEqual(
            rules("tr", tts_provider="slow_tts_example")["tts"]["checks"]["high_latency"]["max_latency_ms"], 2500)

    def test_invalid_policy_action_is_rejected(self):
        text = RULES_PATH.read_text().replace("action: retry_tts", "action: reboot_everything")
        with self.assertRaises(RuleConfigError):
            load_ruleset_from_text(text)

    def test_invalid_severity_is_rejected(self):
        text = RULES_PATH.read_text().replace("severity: warning", "severity: meh", 1)
        with self.assertRaises(RuleConfigError):
            load_ruleset_from_text(text)
