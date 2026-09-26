from django.contrib import admin

from .models import Evaluation


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "call_id", "bot_id", "language", "stt_provider", "tts_provider",
                    "quality_score", "action", "action_target", "policy_name")
    list_filter = ("action", "language", "stt_provider", "tts_provider", "bot_id", "created_at")
    search_fields = ("call_id", "bot_id", "turn_id", "explanation")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in Evaluation._meta.fields]

    def has_add_permission(self, request):
        return False  # evaluations are only created through the API
