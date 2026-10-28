import mimetypes

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework.authtoken.models import Token

from .models import EmergencyRequest, IncidentReport


def _token_user(request):
    auth_header = request.headers.get("Authorization", "")
    scheme, _, key = auth_header.partition(" ")
    if scheme.lower() != "token" or not key:
        return None
    token = Token.objects.filter(key=key, user__is_active=True).select_related("user").first()
    return token.user if token else None


def _serve_image(image_field):
    if not image_field:
        raise Http404
    try:
        content_type = mimetypes.guess_type(image_field.name)[0] or "application/octet-stream"
        return FileResponse(image_field.open("rb"), content_type=content_type)
    except (OSError, ValueError):
        raise Http404


def secure_report_image(request, ref_no):
    report = get_object_or_404(IncidentReport.objects.select_related("reporter"), ref_no=ref_no)
    allowed = bool(
        request.user.is_authenticated
        and (
            request.user.is_dashboard_user
            or (request.user.role == "reporter" and report.reporter_id == request.user.id)
        )
    )
    if not allowed:
        user = _token_user(request)
        allowed = bool(user and (user.id == report.reporter_id or user.role == "admin"))
    if not allowed:
        raise Http404
    return _serve_image(report.image)


def secure_emergency_request_image(request, ref_no):
    emergency = get_object_or_404(
        EmergencyRequest.objects.select_related("requester"), ref_no=ref_no
    )
    allowed = bool(
        request.user.is_authenticated
        and (
            request.user.is_dashboard_user
            or (request.user.role == "reporter" and emergency.requester_id == request.user.id)
        )
    )
    if not allowed:
        user = _token_user(request)
        allowed = bool(user and (user.id == emergency.requester_id or user.role == "admin"))
    if not allowed:
        raise Http404
    return _serve_image(emergency.image)
