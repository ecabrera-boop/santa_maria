from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from api.health import healthz
from dashboard.views import StaffLoginView, dashboard_home
from incidents.views import secure_emergency_request_image, secure_report_image

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", StaffLoginView.as_view(), name="staff-login"),
    path("dashboard/", include("dashboard.urls")),
    path("reports/<str:ref_no>/image/", secure_report_image, name="secure-report-image"),
    path(
        "emergency-requests/<str:ref_no>/image/",
        secure_emergency_request_image,
        name="secure-emergency-request-image",
    ),
    path("api/v1/", include("api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("healthz/", healthz, name="healthz"),
    path("", dashboard_home, name="home"),
]
