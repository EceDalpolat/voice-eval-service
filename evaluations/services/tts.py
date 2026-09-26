"""TTS (text-to-speech) output evaluation."""
from __future__ import annotations

from .audio import evaluate_audio
from .providers import normalize_tts
from .types import CheckRunner, ComponentResult


def evaluate_tts(tts: dict | None, rules: dict, tts_audio: bytes | None = None) -> ComponentResult:
    if not tts:
        return ComponentResult.skipped("tts", "no TTS output provided")

    norm = normalize_tts(tts)
    runner = CheckRunner("tts", rules["tts"]["checks"])
    signals = {"normalized": norm.to_dict()}
    findings = []

    # If the generated audio itself is sent, re-use the audio checks on it.
    if tts_audio:
        audio_result = evaluate_audio(
            tts_audio, rules, component="tts", prefix="tts.audio",
            only_checks=rules["tts"].get("audio_checks", []),
        )
        signals["audio"] = audio_result.signals
        findings.extend(audio_result.findings)
        if norm.duration_ms is None and "duration_ms" in audio_result.signals:
            norm.duration_ms = audio_result.signals["duration_ms"]
            signals["normalized"]["duration_ms"] = norm.duration_ms

    text = norm.text.strip()
    runner.add("empty_text", bool(text), len(text), "> 0 characters",
               "TTS input text is empty." if not text else "TTS input text is not empty.")

    if norm.duration_ms is not None:
        runner.add("empty_audio", norm.duration_ms > 0, norm.duration_ms, "> 0 ms",
                   f"Generated audio duration is {norm.duration_ms} ms.")

    cps = rules.get("lexicon", {}).get("tts_chars_per_second")
    if (c := runner.cfg("duration_anomaly")) and text and cps and norm.duration_ms:
        expected = len(text) / cps * 1000.0
        ratio = round(norm.duration_ms / expected, 3)
        signals["expected_duration_ms"] = round(expected, 1)
        signals["duration_ratio"] = ratio
        ok = c["min_ratio"] <= ratio <= c["max_ratio"]
        runner.add("duration_anomaly", ok, ratio, [c["min_ratio"], c["max_ratio"]],
                   f"Audio is {norm.duration_ms:.0f} ms for {len(text)} characters; expected about "
                   f"{expected:.0f} ms (ratio {ratio}, allowed {c['min_ratio']}-{c['max_ratio']}).")

    if norm.latency_ms is not None:
        for name in ("high_latency", "very_high_latency"):
            if c := runner.cfg(name):
                t = c["max_latency_ms"]
                runner.add(name, norm.latency_ms <= t, norm.latency_ms, t,
                           f"TTS latency {norm.latency_ms} ms (maximum {t} ms).")

    findings.extend(runner.findings)
    return ComponentResult(component="tts", evaluated=True, signals=signals, findings=findings)
