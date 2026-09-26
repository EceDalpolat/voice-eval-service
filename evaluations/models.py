import uuid

from django.db import models

from .services.types import ACTIONS


class Evaluation(models.Model):
    """One evaluated voice turn together with its recovery decision.

    Evaluation and decision are stored in one row on purpose: they are
    always created together, read together, and every reporting query
    filters on both (e.g. "retry rate per provider").
    """

    ACTION_CHOICES = [(a, a) for a in ACTIONS]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # --- identity / filter fields
    call_id = models.CharField(max_length=128, db_index=True)
    turn_id = models.CharField(max_length=128, blank=True, default="")
    bot_id = models.CharField(max_length=128, db_index=True)
    language = models.CharField(max_length=8)
    stt_provider = models.CharField(max_length=64, blank=True, default="", db_index=True)
    tts_provider = models.CharField(max_length=64, blank=True, default="", db_index=True)
    stt_attempt = models.PositiveSmallIntegerField(default=1)
    tts_attempt = models.PositiveSmallIntegerField(default=1)

    # --- denormalised metrics for fast aggregation
    stt_latency_ms = models.FloatField(null=True, blank=True)
    tts_latency_ms = models.FloatField(null=True, blank=True)
    audio_score = models.FloatField(null=True, blank=True)
    stt_score = models.FloatField(null=True, blank=True)
    tts_score = models.FloatField(null=True, blank=True)
    quality_score = models.FloatField(null=True, blank=True)
    audio_failed = models.BooleanField(default=False)
    stt_failed = models.BooleanField(default=False)
    tts_failed = models.BooleanField(default=False)

    # --- decision
    action = models.CharField(max_length=32, choices=ACTION_CHOICES, db_index=True)
    action_target = models.CharField(max_length=16, blank=True, default="")
    policy_name = models.CharField(max_length=128)
    explanation = models.TextField()
    triggered_by = models.JSONField(default=list)
    decision_trace = models.JSONField(default=list)

    # --- full detail for auditing / re-evaluation
    request_payload = models.JSONField()   # audio bytes replaced by hash + size
    signals = models.JSONField()           # per component signals + findings
    rules_version = models.CharField(max_length=32)
    rules_fingerprint = models.CharField(max_length=16)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["call_id", "created_at"]),
            models.Index(fields=["bot_id", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.call_id} -> {self.action} ({self.quality_score})"
