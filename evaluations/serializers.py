import base64
import binascii

from django.conf import settings
from rest_framework import serializers

from .models import Evaluation


def decode_audio_b64(value: str) -> bytes:
    if "," in value and value.strip().startswith("data:"):
        value = value.split(",", 1)[1]  # accept data URLs
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise serializers.ValidationError("Invalid base64 audio.")
    if len(data) > settings.VOICE_EVAL_MAX_AUDIO_BYTES:
        raise serializers.ValidationError("Audio is larger than the allowed limit.")
    return data


class STTInputSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=64)
    transcript = serializers.CharField(allow_blank=True, trim_whitespace=False)
    confidence = serializers.FloatField(min_value=0.0, max_value=1.0, required=False, allow_null=True)
    latency_ms = serializers.FloatField(min_value=0, required=False, allow_null=True)
    attempt = serializers.IntegerField(min_value=1, default=1)


class TTSInputSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=64)
    text = serializers.CharField(allow_blank=True, trim_whitespace=False)
    duration_ms = serializers.FloatField(min_value=0, required=False, allow_null=True)
    latency_ms = serializers.FloatField(min_value=0, required=False, allow_null=True)
    sample_rate = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    attempt = serializers.IntegerField(min_value=1, default=1)
    provider_metadata = serializers.DictField(required=False, default=dict)
    audio_base64 = serializers.CharField(required=False, write_only=True,
                                         help_text="Optional generated TTS audio (PCM WAV, base64).")


class EvaluationRequestSerializer(serializers.Serializer):
    call_id = serializers.CharField(max_length=128)
    bot_id = serializers.CharField(max_length=128)
    turn_id = serializers.CharField(max_length=128, required=False, default="")
    language = serializers.CharField(max_length=8, default="tr")
    audio_base64 = serializers.CharField(required=False, write_only=True,
                                         help_text="Optional caller audio (PCM WAV, base64).")
    stt = STTInputSerializer(required=False)
    tts = TTSInputSerializer(required=False)

    def validate_audio_base64(self, value):
        return decode_audio_b64(value)

    def validate(self, attrs):
        if not attrs.get("stt") and not attrs.get("tts"):
            raise serializers.ValidationError("At least one of 'stt' or 'tts' is required.")
        tts = attrs.get("tts")
        if tts and tts.get("audio_base64"):
            tts["audio_base64"] = decode_audio_b64(tts["audio_base64"])
        return attrs


class EvaluationSerializer(serializers.ModelSerializer):
    decision = serializers.SerializerMethodField()
    scores = serializers.SerializerMethodField()

    class Meta:
        model = Evaluation
        fields = [
            "id", "call_id", "turn_id", "bot_id", "language",
            "stt_provider", "tts_provider", "stt_attempt", "tts_attempt",
            "stt_latency_ms", "tts_latency_ms",
            "quality_score", "scores", "decision",
            "signals", "request_payload", "rules_version", "rules_fingerprint", "created_at",
        ]

    def get_decision(self, obj) -> dict:
        return {
            "action": obj.action,
            "target": obj.action_target or None,
            "policy": obj.policy_name,
            "explanation": obj.explanation,
            "triggered_by": obj.triggered_by,
            "trace": obj.decision_trace,
        }

    def get_scores(self, obj) -> dict:
        return {"audio": obj.audio_score, "stt": obj.stt_score, "tts": obj.tts_score}


class EvaluationListSerializer(serializers.ModelSerializer):
    """Compact form for list endpoints (no heavy signals / trace)."""

    scores = serializers.SerializerMethodField()

    class Meta:
        model = Evaluation
        fields = [
            "id", "call_id", "turn_id", "bot_id", "language", "stt_provider", "tts_provider",
            "quality_score", "scores", "action", "action_target", "policy_name", "created_at",
        ]

    def get_scores(self, obj) -> dict:
        return {"audio": obj.audio_score, "stt": obj.stt_score, "tts": obj.tts_score}


class ActionCountSerializer(serializers.Serializer):
    action = serializers.CharField()
    count = serializers.IntegerField()


class ProviderStatsSerializer(serializers.Serializer):
    provider = serializers.CharField()
    evaluations = serializers.IntegerField()
    avg_latency_ms = serializers.FloatField(allow_null=True)
    max_latency_ms = serializers.FloatField(allow_null=True)
    avg_score = serializers.FloatField(allow_null=True)
    failure_rate = serializers.FloatField()


class StatsSerializer(serializers.Serializer):
    total_evaluations = serializers.IntegerField()
    avg_quality_score = serializers.FloatField(allow_null=True)
    avg_component_scores = serializers.DictField()
    failure_rate = serializers.FloatField()
    retry_rate = serializers.FloatField()
    actions = ActionCountSerializer(many=True)
    stt_providers = ProviderStatsSerializer(many=True)
    tts_providers = ProviderStatsSerializer(many=True)
