import json

from django.shortcuts import render
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Evaluation
from .repository import compute_stats, create_evaluation, filter_evaluations
from .serializers import (
    EvaluationListSerializer,
    EvaluationRequestSerializer,
    EvaluationSerializer,
    StatsSerializer,
)
from .services.rules import load_ruleset

FILTER_PARAMS = [
    OpenApiParameter("call_id", str),
    OpenApiParameter("bot_id", str),
    OpenApiParameter("provider", str, description="Matches STT or TTS provider"),
    OpenApiParameter("stt_provider", str),
    OpenApiParameter("tts_provider", str),
    OpenApiParameter("action", str, enum=[c[0] for c in Evaluation.ACTION_CHOICES]),
    OpenApiParameter("language", str),
    OpenApiParameter("date_from", OpenApiTypes.STR, description="YYYY-MM-DD or ISO datetime (inclusive)"),
    OpenApiParameter("date_to", OpenApiTypes.STR, description="YYYY-MM-DD or ISO datetime (inclusive)"),
]


def _request_data(request) -> dict:
    """JSON body, or multipart with a `data` JSON field plus `audio` /
    `tts_audio` WAV files. Files are converted to the same base64 fields so
    both formats go through one serializer."""
    if request.content_type and request.content_type.startswith("multipart/"):
        import base64
        try:
            data = json.loads(request.data.get("data") or "{}")
        except json.JSONDecodeError:
            return {"__invalid__": "Field 'data' must contain JSON."}
        if f := request.FILES.get("audio"):
            data["audio_base64"] = base64.b64encode(f.read()).decode()
        if (f := request.FILES.get("tts_audio")) and isinstance(data.get("tts"), dict):
            data["tts"]["audio_base64"] = base64.b64encode(f.read()).decode()
        return data
    return request.data


class EvaluationListCreateView(generics.GenericAPIView):
    parser_classes = [JSONParser, MultiPartParser]
    serializer_class = EvaluationListSerializer

    def get_queryset(self):
        return filter_evaluations(self.request.query_params)

    @extend_schema(parameters=FILTER_PARAMS, responses=EvaluationListSerializer(many=True),
                   summary="List evaluations with filters")
    def get(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(EvaluationListSerializer(page, many=True).data)

    @extend_schema(request=EvaluationRequestSerializer, responses={201: EvaluationSerializer},
                   summary="Evaluate one voice turn and get a recovery decision")
    def post(self, request):
        data = _request_data(request)
        if "__invalid__" in data:
            return Response({"data": [data["__invalid__"]]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = EvaluationRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        evaluation = create_evaluation(serializer.validated_data)
        return Response(EvaluationSerializer(evaluation).data, status=status.HTTP_201_CREATED)


class EvaluationDetailView(generics.RetrieveAPIView):
    queryset = Evaluation.objects.all()
    serializer_class = EvaluationSerializer

    @extend_schema(summary="Retrieve one evaluation with full signals and decision trace")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class EvaluationStatsView(APIView):
    @extend_schema(parameters=FILTER_PARAMS, responses=StatsSerializer,
                   summary="Aggregate quality statistics")
    def get(self, request):
        return Response(compute_stats(filter_evaluations(request.query_params)))


class HealthView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT, summary="Liveness + rule file check")
    def get(self, request):
        rs = load_ruleset()
        return Response({"status": "ok", "rules_version": rs.version, "rules_fingerprint": rs.fingerprint})


def quality_report(request):
    """Small server-rendered quality report (same filters as the API)."""
    try:
        qs = filter_evaluations(request.GET)
        error = None
    except ValidationError as exc:
        qs, error = Evaluation.objects.all(), exc.detail
    return render(request, "evaluations/report.html", {
        "stats": compute_stats(qs),
        "recent": qs[:25],
        "filters": request.GET,
        "error": error,
        "actions": [c[0] for c in Evaluation.ACTION_CHOICES],
    }, status=400 if error else 200)
