from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from evaluations.views import quality_report

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("evaluations.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("report/", quality_report, name="quality-report"),
]
