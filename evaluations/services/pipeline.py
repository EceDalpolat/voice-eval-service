"""Orchestrates one turn evaluation: rules -> evaluators -> score -> policy.

This is the single entry point used by the API (and by a background worker
if evaluation is moved to a task queue later).
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio import evaluate_audio
from .policy import Decision, PolicyContext, decide
from .rules import RuleSet
from .stt import evaluate_stt
from .tts import evaluate_tts
from .types import ComponentResult, score_component


@dataclass
class TurnEvaluation:
    language: str
    components: dict[str, ComponentResult]
    quality_score: float | None
    decision: Decision
    rules_version: str
    rules_fingerprint: str

    def component_score(self, name: str) -> float | None:
        return self.components[name].score

    def signals_dict(self) -> dict:
        return {name: c.to_dict() for name, c in self.components.items()}


def overall_score(components: dict[str, ComponentResult], weights: dict) -> float | None:
    parts = [(weights.get(n, 0.0), c.score) for n, c in components.items() if c.evaluated and c.score is not None]
    total_weight = sum(w for w, _ in parts)
    if not total_weight:
        return None
    return round(sum(w * s for w, s in parts) / total_weight, 4)


def evaluate_turn(payload: dict, ruleset: RuleSet, *, audio: bytes | None = None,
                  tts_audio: bytes | None = None, prior_failures: int = 0) -> TurnEvaluation:
    stt = payload.get("stt")
    tts = payload.get("tts")
    rules = ruleset.resolve(
        payload.get("language"),
        stt_provider=(stt or {}).get("provider"),
        tts_provider=(tts or {}).get("provider"),
    )
    fail_below = rules["scoring"]["component_fail_below"]

    audio_result = score_component(evaluate_audio(audio, rules), fail_below)
    audio_ms = audio_result.signals.get("duration_ms") if audio_result.evaluated else None
    stt_result = score_component(evaluate_stt(stt, rules, audio_duration_ms=audio_ms), fail_below)
    tts_result = score_component(evaluate_tts(tts, rules, tts_audio=tts_audio), fail_below)

    components = {"audio": audio_result, "stt": stt_result, "tts": tts_result}
    ctx = PolicyContext(
        components=components,
        attempts={"stt": (stt or {}).get("attempt", 1), "tts": (tts or {}).get("attempt", 1)},
        prior_failures=prior_failures,
    )
    return TurnEvaluation(
        language=rules["language"],
        components=components,
        quality_score=overall_score(components, rules["scoring"]["component_weights"]),
        decision=decide(ctx, rules["policy"]),
        rules_version=ruleset.version,
        rules_fingerprint=ruleset.fingerprint,
    )
