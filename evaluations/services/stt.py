"""STT (speech-to-text) result evaluation.

Checks are driven by the resolved rule config; language specific data
(filler words, alphabet) comes from `rules["lexicon"]`.
"""
from __future__ import annotations

import re
from collections import Counter

from .types import CheckRunner, ComponentResult

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
ALLOWED_SYMBOLS = set(" .,!?'’\"-:;()%&/")


def turkish_aware_lower(text: str, language: str) -> str:
    # Python's lower() maps "I" -> "i"; in Turkish it must be "ı" (and "İ" -> "i").
    if language == "tr":
        text = text.replace("I", "ı").replace("İ", "i")
    return text.lower()


def tokenize(text: str, language: str) -> list[str]:
    return TOKEN_RE.findall(turkish_aware_lower(text, language))


def garbage_ratio(text: str, alphabet: str, language: str) -> float:
    """Share of characters that are neither letters of the language,
    digits nor normal punctuation. High values mean encoding junk or
    wrong-language output."""
    chars = [c for c in turkish_aware_lower(text, language) if not c.isspace()]
    if not chars:
        return 0.0
    allowed = set(alphabet)
    bad = sum(1 for c in chars if not (c in allowed or c.isdigit() or c in ALLOWED_SYMBOLS))
    return bad / len(chars)


def evaluate_stt(stt: dict | None, rules: dict, audio_duration_ms: float | None = None) -> ComponentResult:
    if not stt:
        return ComponentResult.skipped("stt", "no STT result provided")

    language = rules["language"]
    lexicon = rules.get("lexicon", {})
    runner = CheckRunner("stt", rules["stt"]["checks"])
    text = (stt.get("transcript") or "").strip()
    tokens = tokenize(text, language)
    confidence = stt.get("confidence")
    latency = stt.get("latency_ms")

    signals = {
        "provider": stt.get("provider"),
        "attempt": stt.get("attempt", 1),
        "transcript_length": len(text),
        "token_count": len(tokens),
        "confidence": confidence,
        "latency_ms": latency,
    }

    runner.add("empty_transcript", bool(text), len(text), "> 0 characters",
               "Transcript is empty." if not text else "Transcript is not empty.")

    if text:
        fillers = {f.lower() for f in lexicon.get("filler_words", [])}
        if fillers:
            filler_only = bool(tokens) and all(t in fillers for t in tokens)
            runner.add("filler_only", not filler_only, text, "at least one non-filler word",
                       f"Transcript '{text}' contains only filler words." if filler_only
                       else "Transcript contains real words.")

        if (c := runner.cfg("garbage_characters")) and lexicon.get("alphabet"):
            ratio = round(garbage_ratio(text, lexicon["alphabet"], language), 4)
            signals["garbage_ratio"] = ratio
            runner.add("garbage_characters", ratio <= c["max_ratio"], ratio, c["max_ratio"],
                       f"{ratio:.0%} of characters are unexpected for language '{language}' "
                       f"(maximum {c['max_ratio']:.0%}).")

        if (c := runner.cfg("repeated_tokens")) and len(tokens) >= c["min_tokens"]:
            word, count = Counter(tokens).most_common(1)[0]
            ratio = round(count / len(tokens), 4)
            signals["max_repeat_ratio"] = ratio
            runner.add("repeated_tokens", ratio <= c["max_repeat_ratio"], ratio, c["max_repeat_ratio"],
                       f"Word '{word}' makes up {ratio:.0%} of the transcript "
                       f"(maximum {c['max_repeat_ratio']:.0%}); possible STT hallucination.")

        if (c := runner.cfg("speech_rate")) and audio_duration_ms and audio_duration_ms >= c["min_audio_ms"]:
            wps = round(len(tokens) / (audio_duration_ms / 1000.0), 3)
            signals["words_per_second"] = wps
            runner.add("speech_rate", wps >= c["min_words_per_second"], wps, c["min_words_per_second"],
                       f"{wps} words/second for {audio_duration_ms:.0f} ms of audio "
                       f"(minimum {c['min_words_per_second']}); speech may have been dropped.")

    if (c := runner.cfg("low_confidence")) and confidence is not None:
        t = c["min_confidence"]
        runner.add("low_confidence", confidence >= t, confidence, t,
                   f"STT confidence {confidence} (minimum {t}).")

    if (c := runner.cfg("high_latency")) and latency is not None:
        t = c["max_latency_ms"]
        runner.add("high_latency", latency <= t, latency, t, f"STT latency {latency} ms (maximum {t} ms).")

    return ComponentResult(component="stt", evaluated=True, signals=signals, findings=runner.findings)
