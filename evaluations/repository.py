"""Database side of the service: persisting evaluations, filtering, stats.

Views stay thin; the pure evaluation logic lives in `services/`, the ORM
code lives here.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, time

from django.db import transaction
from django.db.models import Avg, Count, Max, Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import ValidationError

from .models import Evaluation
from .services.pipeline import evaluate_turn
from .services.rules import load_ruleset
from .services.types import ACCEPT, RETRY_STT, RETRY_TTS


def _audio_ref(data: bytes | None) -> dict | None:
    if not data:
        return None
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def prior_failures_in_call(call_id: str) -> int:
    return Evaluation.objects.filter(call_id=call_id).exclude(action=ACCEPT).count()


@transaction.atomic
def create_evaluation(data: dict) -> Evaluation:
    """Run the evaluation pipeline for validated request data and persist it."""
    data = dict(data)
    audio = data.pop("audio_base64", None)
    stt = dict(data["stt"]) if data.get("stt") else None
    tts = dict(data["tts"]) if data.get("tts") else None
    tts_audio = tts.pop("audio_base64", None) if tts else None

    payload = {**data, "stt": stt, "tts": tts}
    result = evaluate_turn(
        payload, load_ruleset(), audio=audio, tts_audio=tts_audio,
        prior_failures=prior_failures_in_call(data["call_id"]),
    )

    stored_payload = {**payload, "audio": _audio_ref(audio)}
    if tts is not None:
        stored_payload["tts"] = {**tts, "audio": _audio_ref(tts_audio)}

    tts_norm = result.components["tts"].signals.get("normalized", {})
    d = result.decision
    return Evaluation.objects.create(
        call_id=data["call_id"],
        turn_id=data.get("turn_id", ""),
        bot_id=data["bot_id"],
        language=result.language,
        stt_provider=(stt or {}).get("provider", ""),
        tts_provider=(tts or {}).get("provider", ""),
        stt_attempt=(stt or {}).get("attempt", 1),
        tts_attempt=(tts or {}).get("attempt", 1),
        stt_latency_ms=(stt or {}).get("latency_ms"),
        tts_latency_ms=tts_norm.get("latency_ms"),
        audio_score=result.component_score("audio"),
        stt_score=result.component_score("stt"),
        tts_score=result.component_score("tts"),
        quality_score=result.quality_score,
        audio_failed=result.components["audio"].failed,
        stt_failed=result.components["stt"].failed,
        tts_failed=result.components["tts"].failed,
        action=d.action,
        action_target=d.target or "",
        policy_name=d.policy,
        explanation=d.explanation,
        triggered_by=d.triggered_by,
        decision_trace=d.trace,
        request_payload=stored_payload,
        signals=result.signals_dict(),
        rules_version=result.rules_version,
        rules_fingerprint=result.rules_fingerprint,
    )


def _parse_boundary(value: str, name: str, end_of_day: bool) -> datetime:
    # A plain date must be checked first: parse_datetime also accepts
    # "YYYY-MM-DD" (as midnight), which would make date_to exclusive.
    try:
        d = parse_date(value)
        dt = parse_datetime(value) if d is None else None
    except ValueError:
        d = dt = None
    if d is not None:
        dt = datetime.combine(d, time.max if end_of_day else time.min)
    elif dt is None:
        raise ValidationError({name: "Use ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."})
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def filter_evaluations(params) -> QuerySet[Evaluation]:
    """Filters: call_id, bot_id, provider (STT or TTS), stt_provider,
    tts_provider, action, language, date_from, date_to (inclusive)."""
    qs = Evaluation.objects.all()
    for field in ("call_id", "bot_id", "stt_provider", "tts_provider", "language"):
        if params.get(field):
            qs = qs.filter(**{field: params[field]})
    if provider := params.get("provider"):
        qs = qs.filter(Q(stt_provider=provider) | Q(tts_provider=provider))
    if action := params.get("action"):
        valid = {c[0] for c in Evaluation.ACTION_CHOICES}
        if action not in valid:
            raise ValidationError({"action": f"Must be one of {sorted(valid)}."})
        qs = qs.filter(action=action)
    if params.get("date_from"):
        qs = qs.filter(created_at__gte=_parse_boundary(params["date_from"], "date_from", False))
    if params.get("date_to"):
        qs = qs.filter(created_at__lte=_parse_boundary(params["date_to"], "date_to", True))
    return qs


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _provider_stats(qs: QuerySet, provider_field: str, latency_field: str, score_field: str,
                    failed_field: str) -> list[dict]:
    """Per provider numbers. failure_rate here is the share of turns in which
    THIS provider's component failed, not the overall turn action."""
    rows = (
        qs.exclude(**{provider_field: ""})
        .values(provider_field)
        .annotate(
            evaluations=Count("id"),
            avg_latency_ms=Avg(latency_field),
            max_latency_ms=Max(latency_field),
            avg_score=Avg(score_field),
            failures=Count("id", filter=Q(**{failed_field: True})),
        )
        .order_by(provider_field)
    )
    return [
        {
            "provider": r[provider_field],
            "evaluations": r["evaluations"],
            "avg_latency_ms": None if r["avg_latency_ms"] is None else round(r["avg_latency_ms"], 1),
            "max_latency_ms": r["max_latency_ms"],
            "avg_score": None if r["avg_score"] is None else round(r["avg_score"], 4),
            "failure_rate": _rate(r["failures"], r["evaluations"]),
        }
        for r in rows
    ]


def compute_stats(qs: QuerySet[Evaluation]) -> dict:
    agg = qs.aggregate(
        total=Count("id"),
        avg_quality=Avg("quality_score"),
        avg_audio=Avg("audio_score"),
        avg_stt=Avg("stt_score"),
        avg_tts=Avg("tts_score"),
        failures=Count("id", filter=~Q(action=ACCEPT)),
        retries=Count("id", filter=Q(action__in=[RETRY_STT, RETRY_TTS])),
    )
    total = agg["total"]

    def r(v):
        return None if v is None else round(v, 4)

    return {
        "total_evaluations": total,
        "avg_quality_score": r(agg["avg_quality"]),
        "avg_component_scores": {"audio": r(agg["avg_audio"]), "stt": r(agg["avg_stt"]), "tts": r(agg["avg_tts"])},
        "failure_rate": _rate(agg["failures"], total),
        "retry_rate": _rate(agg["retries"], total),
        "actions": list(qs.values("action").annotate(count=Count("id")).order_by("-count", "action")),
        "stt_providers": _provider_stats(qs, "stt_provider", "stt_latency_ms", "stt_score", "stt_failed"),
        "tts_providers": _provider_stats(qs, "tts_provider", "tts_latency_ms", "tts_score", "tts_failed"),
    }
