from django.urls import path

from . import views

urlpatterns = [
    path("evaluations/", views.EvaluationListCreateView.as_view(), name="evaluation-list"),
    path("evaluations/stats/", views.EvaluationStatsView.as_view(), name="evaluation-stats"),
    path("evaluations/<uuid:pk>/", views.EvaluationDetailView.as_view(), name="evaluation-detail"),
    path("health/", views.HealthView.as_view(), name="health"),
]
