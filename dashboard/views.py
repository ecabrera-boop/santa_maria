import csv
import logging
import mimetypes
import os
import re
import tempfile
import threading
import zipfile
from datetime import timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import FormView

from accounts.models import User, UserManagementLog
from classifier.models import ModelVersion
from classifier.services import ImageValidationError, classify_image
from evaluation.models import TestRecord, TestRun
from incidents.models import Barangay, EmergencyRequest, IncidentMapPin, IncidentReport, StatusLog
from incidents.notifications import notify_emergency_requester, notify_reporter

from .forms import DashboardUserForm, StaffLoginForm

logger = logging.getLogger(__name__)

# Landmark / GPS text from the mobile app, e.g. "Place (16.73489, 120.36453)" or "GPS: 16.73, 120.36"
_LAT_LNG_RE = re.compile(
    r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)"
    r"|(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)"
)


def parse_lat_lng(text):
    if not text:
        return None
    match = _LAT_LNG_RE.search(str(text))
    if not match:
        return None
    if match.group(1) is not None:
        lat, lng = float(match.group(1)), float(match.group(2))
    else:
        lat, lng = float(match.group(3)), float(match.group(4))
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return lat, lng
    return None


def dashboard_user(user):
    return bool(user.is_authenticated and user.is_dashboard_user)


def staff_required(view):
    from django.contrib.auth.decorators import user_passes_test

    return user_passes_test(dashboard_user, login_url="/login/")(view)


def admin_required(view):
    from django.contrib.auth.decorators import user_passes_test

    return user_passes_test(
        lambda user: bool(user.is_authenticated and getattr(user, "is_admin_role", False)),
        login_url="/login/",
    )(view)


def nav_counts():
    new_count = IncidentReport.objects.filter(status=IncidentReport.Status.NEW).count()
    pending_count = EmergencyRequest.objects.filter(status=EmergencyRequest.Status.PENDING).count()
    return {
        "new_count": new_count,
        "pending_count": pending_count,
        "inbox_count": new_count + pending_count,
    }


def _incident_inbox_row(report):
    reporter = report.reporter
    return {
        "kind": "incident",
        "kind_label": "Incident",
        "ref_no": report.ref_no,
        "category": report.predicted_category,
        "category_display": report.get_predicted_category_display(),
        "barangay": report.barangay,
        "reporter_name": reporter.full_name if reporter else "Unknown reporter",
        "contact": (reporter.mobile_number if reporter else "") or report.contact_number or "No contact",
        "submitted_at": report.sent_at or report.created_at,
        "status": report.status,
        "status_display": report.get_status_display(),
        "needs_review": report.status == IncidentReport.Status.NEW,
        "landmark": report.landmark,
        "image_url": f"/reports/{report.ref_no}/image/",
        "has_image": bool(report.image),
        "detail_url_name": "dashboard-incident-detail",
        "is_guest": False,
    }


def _emergency_inbox_row(item):
    if item.reporter_name:
        reporter_name = item.reporter_name
    elif item.requester:
        reporter_name = item.requester.full_name
    else:
        reporter_name = "Guest reporter"
    if item.contact_number:
        contact = item.contact_number
    elif item.requester:
        contact = item.requester.mobile_number
    else:
        contact = "No contact"
    return {
        "kind": "emergency",
        "kind_label": "Emergency",
        "ref_no": item.ref_no,
        "category": item.category,
        "category_display": item.get_category_display(),
        "barangay": item.barangay,
        "reporter_name": reporter_name,
        "contact": contact,
        "submitted_at": item.created_at,
        "status": item.status,
        "status_display": item.get_status_display(),
        "needs_review": item.status == EmergencyRequest.Status.PENDING,
        "landmark": item.landmark,
        "image_url": f"/emergency-requests/{item.ref_no}/image/" if item.image else "",
        "has_image": bool(item.image),
        "detail_url_name": "dashboard-emergency-request-detail",
        "is_guest": item.requester_id is None,
    }


def build_report_inbox(request):
    """Merged incident + emergency queue for one MDRRMO inbox."""
    tab = request.GET.get("tab") or "needs_review"
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()

    incidents = IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT).select_related(
        "reporter", "barangay"
    )
    emergencies = EmergencyRequest.objects.select_related("requester", "barangay")

    if tab == "needs_review":
        incidents = incidents.filter(status=IncidentReport.Status.NEW)
        emergencies = emergencies.filter(status=EmergencyRequest.Status.PENDING)
    elif tab == "reviewed":
        incidents = incidents.exclude(status=IncidentReport.Status.NEW)
        emergencies = emergencies.exclude(status=EmergencyRequest.Status.PENDING)
    elif tab != "all":
        tab = "needs_review"
        incidents = incidents.filter(status=IncidentReport.Status.NEW)
        emergencies = emergencies.filter(status=EmergencyRequest.Status.PENDING)

    if category in dict(IncidentReport.Category.choices):
        incidents = incidents.filter(predicted_category=category)
        emergencies = emergencies.filter(category=category)

    if query:
        incidents = incidents.filter(
            Q(ref_no__icontains=query)
            | Q(reporter__full_name__icontains=query)
            | Q(reporter__mobile_number__icontains=query)
            | Q(landmark__icontains=query)
            | Q(contact_number__icontains=query)
            | Q(description__icontains=query)
        )
        emergencies = emergencies.filter(
            Q(ref_no__icontains=query)
            | Q(reporter_name__icontains=query)
            | Q(requester__full_name__icontains=query)
            | Q(requester__mobile_number__icontains=query)
            | Q(contact_number__icontains=query)
            | Q(landmark__icontains=query)
            | Q(description__icontains=query)
        )

    rows = [_incident_inbox_row(report) for report in incidents]
    rows.extend(_emergency_inbox_row(item) for item in emergencies)
    rows.sort(key=lambda row: row["submitted_at"] or timezone.now(), reverse=True)

    counts = {
        "needs_review": (
            IncidentReport.objects.filter(status=IncidentReport.Status.NEW).count()
            + EmergencyRequest.objects.filter(status=EmergencyRequest.Status.PENDING).count()
        ),
        "reviewed": (
            IncidentReport.objects.exclude(status__in=[IncidentReport.Status.DRAFT, IncidentReport.Status.NEW]).count()
            + EmergencyRequest.objects.exclude(status=EmergencyRequest.Status.PENDING).count()
        ),
        "all": (
            IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT).count()
            + EmergencyRequest.objects.count()
        ),
    }
    return rows, counts, tab, query, category


class StaffLoginView(FormView):
    template_name = "dashboard/login.html"
    form_class = StaffLoginForm
    success_url = "/dashboard/"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        ip = self.request.META.get("REMOTE_ADDR", "unknown")
        key = f"eic-staff-login:{ip}"
        attempts = cache.get(key, 0)
        if attempts >= 10:
            form.add_error(None, "Too many attempts. Wait a minute and try again.")
            return self.form_invalid(form)
        user = form.get_user()
        cache.delete(key)
        login(self.request, user)
        return super().form_valid(form)

    def form_invalid(self, form):
        ip = self.request.META.get("REMOTE_ADDR", "unknown")
        key = f"eic-staff-login:{ip}"
        attempts = cache.get(key, 0)
        if attempts >= 9:
            form.add_error(None, "Too many attempts. Wait a minute and try again.")
        cache.set(key, attempts + 1, 60)
        return super().form_invalid(form)

    def post(self, request, *args, **kwargs):
        ip = request.META.get("REMOTE_ADDR", "unknown")
        if cache.get(f"eic-staff-login:{ip}", 0) >= 10:
            form = self.get_form()
            form.add_error(None, "Too many attempts. Wait a minute and try again.")
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)


@staff_required
def dashboard_home(request):
    now = timezone.localtime(timezone.now())
    period = request.GET.get("period", "7")
    days = {"today": 1, "7": 7, "30": 30}.get(period, 7)
    since = now - timedelta(days=days)
    reports = IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT).filter(created_at__gte=since)
    incoming = IncidentReport.objects.filter(status=IncidentReport.Status.NEW).select_related("barangay", "reporter")
    verified = reports.exclude(verified_category__isnull=True)
    awaiting_incidents = IncidentReport.objects.filter(status=IncidentReport.Status.NEW).count()
    awaiting_emergencies = EmergencyRequest.objects.filter(status=EmergencyRequest.Status.PENDING).count()
    stats = {
        "total": reports.count() + EmergencyRequest.objects.filter(created_at__gte=since).count(),
        "awaiting": awaiting_incidents + awaiting_emergencies,
        "verified": verified.count(),
        "confidence": reports.aggregate(value=Avg("confidence"))["value"],
        "processing": reports.aggregate(value=Avg("processing_time_ms"))["value"],
    }
    category_mode = request.GET.get("categories", "predicted")
    category_field = "verified_category" if category_mode == "verified" else "predicted_category"
    category_counts = reports.exclude(**{f"{category_field}__isnull": True}).values(category_field).annotate(count=Count("id"))
    counts_by_category = {row[category_field]: row["count"] for row in category_counts}
    max_count = max(counts_by_category.values(), default=1)
    recent = IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT).select_related(
        "reporter", "barangay", "verified_by"
    )[:10]
    chart = []
    for offset in range(min(days, 30) - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        series = {"date": day.isoformat()}
        for category, _ in IncidentReport.Category.choices:
            series[category] = reports.filter(created_at__date=day, predicted_category=category).count()
        chart.append(series)
    return render(
        request,
        "dashboard/home.html",
        {
            "stats": stats,
            "incoming": incoming[:5],
            "recent": recent,
            "period": period,
            "chart_json": chart,
            "categories": IncidentReport.Category.choices,
            "counts_by_category": counts_by_category,
            "max_count": max_count,
            "category_mode": category_mode,
            "active_page": "dashboard",
            **nav_counts(),
        },
    )


@staff_required
@require_GET
def incoming_json(request):
    reports = IncidentReport.objects.filter(status=IncidentReport.Status.NEW).select_related("barangay")[:5]
    return JsonResponse(
        {
            "reports": [
                {
                    "ref_no": report.ref_no,
                    "category": report.get_predicted_category_display(),
                    "confidence": round(report.confidence * 100),
                    "barangay": report.barangay.name if report.barangay_id else "Location pending",
                    "created_at": timezone.localtime(report.created_at).strftime("%b %d, %I:%M %p"),
                    "image_url": f"/reports/{report.ref_no}/image/",
                    "url": f"/dashboard/incidents/{report.ref_no}/",
                }
                for report in reports
            ]
        }
    )


def filtered_reports(request):
    reports = IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT).select_related(
        "reporter", "barangay", "verified_by"
    )
    query = request.GET.get("q", "").strip()
    if query:
        reports = reports.filter(
            Q(ref_no__icontains=query)
            | Q(reporter__full_name__icontains=query)
            | Q(reporter__mobile_number__icontains=query)
            | Q(landmark__icontains=query)
        )
    category = request.GET.get("category")
    if category in dict(IncidentReport.Category.choices):
        reports = reports.filter(predicted_category=category)
    selected_status = request.GET.get("status")
    if selected_status in dict(IncidentReport.Status.choices):
        reports = reports.filter(status=selected_status)
    barangay_id = request.GET.get("barangay")
    if barangay_id and barangay_id.isdigit():
        reports = reports.filter(barangay_id=barangay_id)
    if request.GET.get("low_confidence") == "1":
        reports = reports.filter(is_low_confidence=True)
    start = request.GET.get("start")
    end = request.GET.get("end")
    if start:
        reports = reports.filter(created_at__date__gte=start)
    if end:
        reports = reports.filter(created_at__date__lte=end)
    tab = request.GET.get("tab")
    if tab == "new":
        reports = reports.filter(status=IncidentReport.Status.NEW)
    elif tab == "verified":
        reports = reports.filter(verification_action=IncidentReport.VerificationAction.CONFIRMED)
    elif tab == "relabeled":
        reports = reports.filter(verification_action=IncidentReport.VerificationAction.RELABELED)
    elif tab == "invalid":
        reports = reports.filter(verification_action=IncidentReport.VerificationAction.INVALID)
    return reports


@staff_required
def incident_list(request):
    rows, counts, tab, query, category = build_report_inbox(request)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="eic-reports.csv"'
        writer = csv.writer(response)
        writer.writerow(["Type", "Reference", "Category", "Barangay", "Reporter", "Contact", "Submitted", "Status"])
        for row in rows:
            writer.writerow(
                [
                    row["kind_label"],
                    row["ref_no"],
                    row["category_display"],
                    row["barangay"].name if row["barangay"] else "",
                    row["reporter_name"],
                    row["contact"],
                    row["submitted_at"],
                    row["status_display"],
                ]
            )
        return response

    page_number = request.GET.get("page", "1")
    page_number = int(page_number) if page_number.isdigit() else 1
    page_size = 25
    total = len(rows)
    page_count = max(1, (total + page_size - 1) // page_size)
    page_number = min(max(page_number, 1), page_count)
    page_items = rows[(page_number - 1) * page_size : page_number * page_size]
    filters = request.GET.copy()
    filters["tab"] = tab
    return render(
        request,
        "dashboard/incidents.html",
        {
            "items": page_items,
            "page": page_number,
            "page_count": page_count,
            "total": total,
            "counts": counts,
            "categories": IncidentReport.Category.choices,
            "filters": filters,
            "active_page": "incidents",
            **nav_counts(),
        },
    )


@staff_required
def incident_detail(request, ref_no):
    report = get_object_or_404(
        IncidentReport.objects.select_related(
            "reporter", "barangay", "verified_by", "model_version", "map_pin"
        ),
        ref_no=ref_no,
    )
    if report.status == IncidentReport.Status.NEW:
        with transaction.atomic():
            report.status = IncidentReport.Status.RECEIVED
            report.save(update_fields=["status"])
            StatusLog.objects.create(
                report=report,
                from_status=IncidentReport.Status.NEW,
                to_status=IncidentReport.Status.RECEIVED,
                note="Report opened by MDRRMO.",
                changed_by=request.user,
            )
            notify_reporter(report, "Received by MDRRMO.")
    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()
        if action in {"confirm", "relabel", "invalid"}:
            category = request.POST.get("verified_category")
            if action != "invalid" and category not in dict(IncidentReport.Category.choices):
                messages.error(request, "Choose a valid incident category.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            if action == "confirm" and category != report.predicted_category:
                messages.error(request, "Choose the predicted category to confirm it, or use relabel.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            if action == "relabel" and category == report.predicted_category:
                messages.error(request, "Choose a different category to relabel this report.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            previous = report.status
            if action == "invalid":
                report.verification_action = IncidentReport.VerificationAction.INVALID
                report.verified_category = None
                report.status = IncidentReport.Status.INVALID
            else:
                report.verified_category = category
                report.verification_action = (
                    IncidentReport.VerificationAction.CONFIRMED
                    if action == "confirm"
                    else IncidentReport.VerificationAction.RELABELED
                )
                report.status = IncidentReport.Status.VERIFIED
            report.verified_by = request.user
            report.verified_at = timezone.now()
            report.save()
            StatusLog.objects.create(
                report=report,
                from_status=previous,
                to_status=report.status,
                note=note or f"Incident type {report.get_verification_action_display().lower()}.",
                changed_by=request.user,
            )
            if action == "invalid":
                notify_reporter(report, "MDRRMO marked this report as invalid.")
            else:
                notify_reporter(report, f"Verified as {report.get_verified_category_display()} by MDRRMO.")
            messages.success(request, "Verification saved.")
            return redirect("dashboard-incident-detail", ref_no=ref_no)
        if action == "status":
            next_status = request.POST.get("status")
            allowed_statuses = {
                IncidentReport.Status.RECEIVED,
                IncidentReport.Status.VERIFIED,
                IncidentReport.Status.RESPONDED,
                IncidentReport.Status.CLOSED,
                IncidentReport.Status.INVALID,
            }
            if next_status not in allowed_statuses:
                messages.error(request, "Choose a valid status.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            previous = report.status
            report.status = next_status
            report.save(update_fields=["status"])
            StatusLog.objects.create(
                report=report,
                from_status=previous,
                to_status=next_status,
                note=note,
                changed_by=request.user,
            )
            notify_reporter(report, f"Your report is now {report.get_status_display()}.")
            messages.success(request, "Status updated.")
            return redirect("dashboard-incident-detail", ref_no=ref_no)
        if action == "map_pin":
            try:
                latitude = float(request.POST.get("latitude", ""))
                longitude = float(request.POST.get("longitude", ""))
            except (TypeError, ValueError):
                messages.error(request, "Enter valid latitude and longitude.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                messages.error(request, "Coordinates are out of range.")
                return redirect("dashboard-incident-detail", ref_no=ref_no)
            pin, _ = IncidentMapPin.objects.update_or_create(
                report=report,
                defaults={
                    "latitude": latitude,
                    "longitude": longitude,
                    "label": request.POST.get("label", "").strip() or report.landmark or report.ref_no,
                    "notes": request.POST.get("map_notes", "").strip(),
                    "mapped_by": request.user,
                },
            )
            messages.success(request, f"Map pin saved at {pin.latitude}, {pin.longitude}.")
            return redirect("dashboard-incident-detail", ref_no=ref_no)
        if action == "clear_map_pin":
            IncidentMapPin.objects.filter(report=report).delete()
            messages.success(request, "Map pin removed.")
            return redirect("dashboard-incident-detail", ref_no=ref_no)
    probability_rows = [
        (category, label, report.probabilities.get(category, 0))
        for category, label in IncidentReport.Category.choices
    ]
    map_pin = IncidentMapPin.objects.filter(report=report).first()
    return render(
        request,
        "dashboard/incident_detail.html",
        {
            "report": report,
            "map_pin": map_pin,
            "probability_rows": probability_rows,
            "status_options": [
                (IncidentReport.Status.RECEIVED, "Received"),
                (IncidentReport.Status.VERIFIED, "Verified"),
                (IncidentReport.Status.RESPONDED, "Responded"),
                (IncidentReport.Status.CLOSED, "Closed"),
                (IncidentReport.Status.INVALID, "Invalid"),
            ],
            "logs": report.status_logs.select_related("changed_by"),
            "categories": IncidentReport.Category.choices,
            "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
            "map_center_lat": settings.EIC_MAP_CENTER_LAT,
            "map_center_lng": settings.EIC_MAP_CENTER_LNG,
            "active_page": "incidents",
            **nav_counts(),
        },
    )


@staff_required
def incident_map(request):
    incidents = (
        IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT)
        .select_related("barangay", "reporter")
        .order_by("-created_at")
    )
    emergencies = EmergencyRequest.objects.select_related("barangay", "requester").order_by("-created_at")

    category = request.GET.get("category")
    category_choices = dict(IncidentReport.Category.choices) | dict(EmergencyRequest.Category.choices)
    if category in category_choices:
        incidents = incidents.filter(
            Q(verified_category=category)
            | Q(verified_category__isnull=True, predicted_category=category)
        )
        emergencies = emergencies.filter(category=category)

    status_filter = request.GET.get("status", "").strip()
    incident_statuses = dict(IncidentReport.Status.choices)
    emergency_statuses = dict(EmergencyRequest.Status.choices)
    if status_filter in incident_statuses and status_filter != IncidentReport.Status.DRAFT:
        incidents = incidents.filter(status=status_filter)
        emergencies = emergencies.none()
    elif status_filter in emergency_statuses:
        emergencies = emergencies.filter(status=status_filter)
        incidents = incidents.none()

    start = request.GET.get("start", "").strip()
    end = request.GET.get("end", "").strip()
    if start:
        incidents = incidents.filter(created_at__date__gte=start)
        emergencies = emergencies.filter(created_at__date__gte=start)
    if end:
        incidents = incidents.filter(created_at__date__lte=end)
        emergencies = emergencies.filter(created_at__date__lte=end)

    incident_list = list(incidents[:200])
    emergency_list = list(emergencies[:200])
    pins_by_report = {
        pin.report_id: pin
        for pin in IncidentMapPin.objects.filter(report_id__in=[report.pk for report in incident_list])
    }

    markers = []
    filtered_items = []
    unmapped = []

    for report in incident_list:
        pin = pins_by_report.get(report.pk)
        coords = (float(pin.latitude), float(pin.longitude)) if pin else parse_lat_lng(report.landmark)
        category_key = report.verified_category or report.predicted_category
        category_label = (
            report.get_verified_category_display()
            if report.verified_category
            else report.get_predicted_category_display()
        )
        item = {
            "kind": "incident",
            "kind_label": "Incident",
            "ref_no": report.ref_no,
            "category": category_key,
            "category_label": category_label,
            "status": report.status,
            "status_label": report.get_status_display(),
            "barangay": report.barangay.name if report.barangay_id else "",
            "label": (pin.label if pin else "") or report.landmark or report.ref_no,
            "url": f"/dashboard/incidents/{report.ref_no}/",
            "image_url": f"/reports/{report.ref_no}/image/" if report.image else "",
            "has_map_pin": coords is not None,
            "submitted_at": report.sent_at or report.created_at,
        }
        filtered_items.append(item)
        if coords:
            markers.append(
                {
                    "kind": item["kind"],
                    "kind_label": item["kind_label"],
                    "ref_no": item["ref_no"],
                    "lat": coords[0],
                    "lng": coords[1],
                    "label": item["label"],
                    "category": item["category"],
                    "category_label": item["category_label"],
                    "status": item["status"],
                    "status_label": item["status_label"],
                    "barangay": item["barangay"],
                    "url": item["url"],
                    "image_url": item["image_url"],
                }
            )
        else:
            unmapped.append(report)

    for emergency in emergency_list:
        coords = parse_lat_lng(emergency.landmark)
        item = {
            "kind": "emergency",
            "kind_label": "Emergency",
            "ref_no": emergency.ref_no,
            "category": emergency.category,
            "category_label": emergency.get_category_display(),
            "status": emergency.status,
            "status_label": emergency.get_status_display(),
            "barangay": emergency.barangay.name if emergency.barangay_id else "",
            "label": emergency.landmark or emergency.ref_no,
            "url": f"/dashboard/emergency-requests/{emergency.ref_no}/",
            "image_url": f"/emergency-requests/{emergency.ref_no}/image/" if emergency.image else "",
            "has_map_pin": coords is not None,
            "submitted_at": emergency.created_at,
        }
        filtered_items.append(item)
        if coords:
            markers.append(
                {
                    "kind": item["kind"],
                    "kind_label": item["kind_label"],
                    "ref_no": item["ref_no"],
                    "lat": coords[0],
                    "lng": coords[1],
                    "label": item["label"],
                    "category": item["category"],
                    "category_label": item["category_label"],
                    "status": item["status"],
                    "status_label": item["status_label"],
                    "barangay": item["barangay"],
                    "url": item["url"],
                    "image_url": item["image_url"],
                }
            )

    filtered_items.sort(key=lambda row: row["submitted_at"] or timezone.now(), reverse=True)
    unmapped = unmapped[:40]
    map_categories = list(
        dict.fromkeys(
            list(IncidentReport.Category.choices) + list(EmergencyRequest.Category.choices)
        )
    )
    statuses = [
        (key, f"Incident · {label}")
        for key, label in IncidentReport.Status.choices
        if key != IncidentReport.Status.DRAFT
    ] + [
        (key, f"Emergency · {label}")
        for key, label in EmergencyRequest.Status.choices
    ]
    return render(
        request,
        "dashboard/incident_map.html",
        {
            "markers_json": markers,
            "filtered_items": filtered_items,
            "unmapped": unmapped,
            "categories": map_categories,
            "statuses": statuses,
            "filters": request.GET,
            "pin_count": len(markers),
            "report_count": len(filtered_items),
            "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
            "map_center_lat": settings.EIC_MAP_CENTER_LAT,
            "map_center_lng": settings.EIC_MAP_CENTER_LNG,
            "map_zoom": settings.EIC_MAP_DEFAULT_ZOOM,
            "active_page": "incident-map",
            **nav_counts(),
        },
    )


@staff_required
@require_POST
def incident_map_save_pin(request):
    ref_no = request.POST.get("ref_no", "").strip()
    report = get_object_or_404(IncidentReport.objects.exclude(status=IncidentReport.Status.DRAFT), ref_no=ref_no)
    try:
        latitude = float(request.POST.get("latitude", ""))
        longitude = float(request.POST.get("longitude", ""))
    except (TypeError, ValueError):
        messages.error(request, "Click the map to set a valid location.")
        return redirect("dashboard-incident-map")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        messages.error(request, "Coordinates are out of range.")
        return redirect("dashboard-incident-map")
    IncidentMapPin.objects.update_or_create(
        report=report,
        defaults={
            "latitude": latitude,
            "longitude": longitude,
            "label": request.POST.get("label", "").strip() or report.landmark or report.ref_no,
            "notes": request.POST.get("notes", "").strip(),
            "mapped_by": request.user,
        },
    )
    messages.success(request, f"Mapped {report.ref_no} on the incident map.")
    return redirect(f"/dashboard/incident-map/?focus={report.ref_no}")


@staff_required
def evaluation_page(request):
    runs = TestRun.objects.select_related("model_version").all()
    selected_id = request.GET.get("run")
    run = runs.filter(pk=selected_id).first() if selected_id and selected_id.isdigit() else runs.first()
    comparison = []
    for model in ModelVersion.objects.all():
        latest = TestRun.objects.filter(model_version=model, status=TestRun.Status.DONE).first()
        comparison.append({"model": model, "run": latest})
    matrix_rows = []
    max_confusion_count = 0
    column_totals = [0] * len(IncidentReport.Category.choices)
    if run and isinstance(run.confusion_matrix, list):
        max_confusion_count = max(
            (max(row, default=0) for row in run.confusion_matrix if isinstance(row, list)),
            default=0,
        )
        for index, ((key, label), values) in enumerate(zip(IncidentReport.Category.choices, run.confusion_matrix)):
            for cell_index, count in enumerate(values):
                if cell_index < len(column_totals):
                    column_totals[cell_index] += count
            matrix_rows.append(
                {
                    "key": key,
                    "label": label,
                    "total": sum(values),
                    "index": index,
                    "cells": [
                        {
                            "value": count,
                            "diagonal": cell_index == index,
                            "alpha": min(0.42, (count / max_confusion_count) * 0.42) if max_confusion_count else 0,
                        }
                        for cell_index, count in enumerate(values)
                    ],
                }
            )
    return render(
        request,
        "dashboard/evaluation.html",
        {
            "runs": runs,
            "run": run,
            "comparison": comparison,
            "categories": IncidentReport.Category.choices,
            "matrix_rows": matrix_rows,
            "max_confusion_count": max_confusion_count,
            "column_totals": column_totals,
            "training_history": run.model_version.training_history if run and run.model_version else {},
            "active_page": "evaluation",
            **nav_counts(),
        },
    )


@staff_required
def evaluation_export(request):
    run = get_object_or_404(TestRun.objects.select_related("model_version"), pk=request.GET.get("run"))
    export_format = request.GET.get("format", "html")
    if export_format == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="eic-evaluation-{run.pk}.csv"'
        writer = csv.writer(response)
        writer.writerow(["EIC classifier evaluation", run.dataset_name])
        writer.writerow(["Model", f"{run.model_version or 'No model'}"])
        writer.writerow(["Source", run.get_source_display()])
        writer.writerow(["Total", run.total_images, "Successful", run.successful, "Failed", run.failed])
        writer.writerow(["Accuracy", run.accuracy, "Macro precision", run.macro_precision, "Macro recall", run.macro_recall, "Macro F1", run.macro_f1])
        writer.writerow([])
        writer.writerow(["Category", "Precision", "Recall", "F1", "Support"])
        for key, label in IncidentReport.Category.choices:
            values = run.per_class.get(key, {})
            writer.writerow([label, values.get("precision"), values.get("recall"), values.get("f1"), values.get("support")])
        writer.writerow([])
        writer.writerow(["Actual / Predicted", *[label for _, label in IncidentReport.Category.choices]])
        for (key, label), row in zip(IncidentReport.Category.choices, run.confusion_matrix):
            writer.writerow([label, *row])
        return response
    matrix_rows = [
        {"label": label, "values": values}
        for (_, label), values in zip(IncidentReport.Category.choices, run.confusion_matrix)
    ]
    html = render_to_string(
        "dashboard/evaluation_export.html",
        {"run": run, "categories": IncidentReport.Category.choices, "matrix_rows": matrix_rows},
    )
    response = HttpResponse(html)
    response["Content-Disposition"] = f'inline; filename="eic-evaluation-{run.pk}.html"'
    return response


@staff_required
def evaluation_image(request, record_id):
    record = get_object_or_404(TestRecord, pk=record_id)
    if not record.image:
        from django.http import Http404

        raise Http404
    from django.http import FileResponse

    content_type = mimetypes.guess_type(record.image.name)[0] or "application/octet-stream"
    return FileResponse(record.image.open("rb"), content_type=content_type)


@staff_required
@require_GET
def evaluation_progress(request, run_id):
    run = get_object_or_404(TestRun, pk=run_id)
    return JsonResponse(
        {
            "status": run.status,
            "total_images": run.total_images,
            "successful": run.successful,
            "failed": run.failed,
            "accuracy": run.accuracy,
        }
    )


def process_test_archive(run_id, archive_path):
    from evaluation.metrics import finish_test_run
    from django.db import close_old_connections

    close_old_connections()
    try:
        run = TestRun.objects.select_related("model_version").get(pk=run_id)
        expected = set(settings.EIC_CATEGORIES)
        with zipfile.ZipFile(archive_path) as archive:
            members = []
            labels_found = set()
            total_uncompressed = 0
            for member in archive.infolist():
                if member.is_dir():
                    continue
                pure_path = PurePosixPath(member.filename)
                if pure_path.is_absolute() or ".." in pure_path.parts or len(pure_path.parts) < 2:
                    continue
                label = pure_path.parts[0].lower()
                if label not in expected:
                    continue
                labels_found.add(label)
                total_uncompressed += member.file_size
                members.append((member, label))
            if total_uncompressed > 500 * 1024 * 1024:
                raise ValueError("The ZIP expands beyond the 500 MB processing limit.")
            missing = expected - labels_found
            if missing:
                raise ValueError(f"Missing category folders: {', '.join(sorted(missing))}.")
            if not members:
                raise ValueError("No images were found inside the category folders.")
            run.total_images = len(members)
            run.save(update_fields=["total_images"])
            for member, label in members:
                image_name = PurePosixPath(member.filename).name[:255]
                try:
                    if member.file_size > settings.EIC_MAX_UPLOAD_MB * 1024 * 1024:
                        raise ImageValidationError(f"Image must be smaller than {settings.EIC_MAX_UPLOAD_MB} MB.")
                    data = archive.read(member)
                    mime_type = mimetypes.guess_type(image_name)[0] or "application/octet-stream"
                    uploaded = SimpleUploadedFile(image_name, data, content_type=mime_type)
                    result = classify_image(uploaded, model_version=run.model_version)
                    if result["is_demo"]:
                        raise RuntimeError("The selected model is unavailable. Demo predictions are not evaluated.")
                    TestRecord.objects.create(
                        run=run,
                        image_name=image_name,
                        image=uploaded,
                        reference_label=label,
                        predicted_label=result["category"],
                        confidence=result["confidence"],
                        is_correct=result["category"] == label,
                        processing_time_ms=result["processing_time_ms"],
                        app_displayed_label=result["category"],
                    )
                except ImageValidationError as exc:
                    message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
                    TestRecord.objects.create(
                        run=run,
                        image_name=image_name,
                        reference_label=label,
                        validation_status=TestRecord.ValidationStatus.FAILED,
                        error_message=message,
                    )
                run.successful = run.records.filter(validation_status=TestRecord.ValidationStatus.OK).count()
                run.failed = run.records.filter(validation_status=TestRecord.ValidationStatus.FAILED).count()
                run.save(update_fields=["successful", "failed"])
        finish_test_run(run)
    except Exception as exc:
        logger.exception("Background evaluation run %s failed", run_id)
        TestRun.objects.filter(pk=run_id).update(status=TestRun.Status.FAILED, finished_at=timezone.now())
    finally:
        close_old_connections()
        try:
            os.unlink(archive_path)
        except OSError:
            pass


@staff_required
@require_POST
def evaluation_start(request):
    uploaded = request.FILES.get("dataset_zip")
    dataset_name = request.POST.get("dataset_name", "").strip()
    if not uploaded or not uploaded.name.lower().endswith(".zip"):
        messages.error(request, "Choose a ZIP file containing the five category folders.")
        return redirect("dashboard-evaluation")
    if uploaded.size > 100 * 1024 * 1024:
        messages.error(request, "The ZIP file must be smaller than 100 MB.")
        return redirect("dashboard-evaluation")
    if not dataset_name:
        messages.error(request, "Enter a name for this test dataset.")
        return redirect("dashboard-evaluation")
    model = ModelVersion.objects.filter(is_active=True).first()
    if not model or not model.model_file:
        messages.error(request, "Upload and activate a trained model before starting evaluation.")
        return redirect("dashboard-evaluation")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_file:
        for chunk in uploaded.chunks():
            temp_file.write(chunk)
        archive_path = temp_file.name
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if not archive.infolist():
                raise zipfile.BadZipFile("The ZIP archive is empty.")
    except (zipfile.BadZipFile, OSError):
        os.unlink(archive_path)
        messages.error(request, "The ZIP archive is damaged or cannot be opened.")
        return redirect("dashboard-evaluation")
    run = TestRun.objects.create(
        model_version=model,
        dataset_name=dataset_name[:180],
        source=TestRun.Source.COMMAND,
        status=TestRun.Status.RUNNING,
    )
    threading.Thread(target=process_test_archive, args=(run.pk, archive_path), daemon=True).start()
    messages.success(request, f"Evaluation started for {dataset_name}. Progress will update when you refresh.")
    return redirect(f"/dashboard/evaluation/?run={run.pk}")


@staff_required
def test_logs_page(request):
    records = TestRecord.objects.select_related("run", "run__model_version")
    run_id = request.GET.get("run")
    result = request.GET.get("result")
    reference = request.GET.get("reference")
    predicted = request.GET.get("predicted")
    if run_id and run_id.isdigit():
        records = records.filter(run_id=run_id)
    if result == "failed":
        records = records.filter(validation_status=TestRecord.ValidationStatus.FAILED)
    elif result == "correct":
        records = records.filter(is_correct=True)
    elif result == "incorrect":
        records = records.filter(is_correct=False)
    if reference in dict(IncidentReport.Category.choices):
        records = records.filter(reference_label=reference)
    if predicted in dict(IncidentReport.Category.choices):
        records = records.filter(predicted_label=predicted)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="eic-test-logs.csv"'
        writer = csv.writer(response)
        writer.writerow(["Test ID", "Image", "Reference", "Predicted", "App displayed", "Confidence", "Correct", "Server ms", "Round trip ms", "Validation", "Created"])
        for record in records:
            writer.writerow([
                record.pk, record.image_name, record.reference_label, record.predicted_label or "",
                record.app_displayed_label or "", record.confidence, record.is_correct, record.processing_time_ms,
                record.client_roundtrip_ms, record.validation_status, record.created_at,
            ])
        return response
    all_records = TestRecord.objects.all()
    correct_count = all_records.filter(is_correct=True).count()
    successful_count = all_records.filter(validation_status=TestRecord.ValidationStatus.OK).count()
    app_total = all_records.exclude(app_displayed_label__isnull=True).count()
    app_agree = all_records.filter(app_displayed_label=models_f("predicted_label")).count() if app_total else 0
    page_number = request.GET.get("page", "1")
    page_number = int(page_number) if page_number.isdigit() else 1
    page_size = 25
    total = records.count()
    page_count = max(1, (total + page_size - 1) // page_size)
    page_number = min(max(page_number, 1), page_count)
    page_items = records[(page_number - 1) * page_size : page_number * page_size]
    return render(
        request,
        "dashboard/test_logs.html",
        {
            "records": page_items,
            "page": page_number,
            "page_count": page_count,
            "runs": TestRun.objects.all(),
            "categories": IncidentReport.Category.choices,
            "filters": request.GET,
            "total_submissions": all_records.count(),
            "successful_count": successful_count,
            "failed_count": all_records.filter(validation_status=TestRecord.ValidationStatus.FAILED).count(),
            "correct_count": correct_count,
            "incorrect_count": all_records.filter(is_correct=False).count(),
            "agreement_percent": (app_agree / app_total * 100) if app_total else None,
            "misclassified": TestRecord.objects.filter(is_correct=False, validation_status="ok").order_by("confidence")[:8],
            "active_page": "test-logs",
            **nav_counts(),
        },
    )


def models_f(field):
    from django.db.models import F

    return F(field)


def dashboard_home_public(request):
    return redirect("dashboard-home")


@staff_required
def emergency_request_list(request):
    """Old URL — redirect into the unified Reports inbox."""
    params = request.GET.copy()
    old_tab = params.pop("tab", [None])[0]
    if old_tab in {"pending", None, ""}:
        params["tab"] = "needs_review"
    elif old_tab in {"approved", "declined"}:
        params["tab"] = "reviewed"
    else:
        params["tab"] = "all"
    query = params.urlencode()
    target = "/dashboard/incidents/"
    if query:
        target = f"{target}?{query}"
    return redirect(target)


@staff_required
def emergency_request_detail(request, ref_no):
    emergency = get_object_or_404(
        EmergencyRequest.objects.select_related("requester", "barangay", "reviewed_by", "incident_report"),
        ref_no=ref_no,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()
        if action in {"approve", "decline"}:
            if emergency.status != EmergencyRequest.Status.PENDING:
                messages.error(request, "This request has already been reviewed.")
                return redirect("dashboard-emergency-request-detail", ref_no=ref_no)
            emergency.status = (
                EmergencyRequest.Status.APPROVED if action == "approve" else EmergencyRequest.Status.DECLINED
            )
            emergency.review_note = note
            emergency.reviewed_by = request.user
            emergency.reviewed_at = timezone.now()
            emergency.save()
            if action == "approve":
                notify_emergency_requester(emergency, "Your emergency request was approved by MDRRMO.")
                messages.success(request, "Emergency request approved.")
            else:
                notify_emergency_requester(emergency, "Your emergency request was declined by MDRRMO.")
                messages.success(request, "Emergency request declined.")
            return redirect("dashboard-emergency-request-detail", ref_no=ref_no)
        messages.error(request, "Choose approve or decline.")
        return redirect("dashboard-emergency-request-detail", ref_no=ref_no)
    return render(
        request,
        "dashboard/emergency_request_detail.html",
        {
            "emergency": emergency,
            "active_page": "incidents",
            **nav_counts(),
        },
    )


@admin_required
def user_management_list(request):
    users = User.objects.select_related("barangay").order_by("full_name", "username")
    role = request.GET.get("role", "")
    status_filter = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()
    if role in dict(User.Role.choices):
        users = users.filter(role=role)
    if status_filter == "active":
        users = users.filter(is_active=True)
    elif status_filter == "inactive":
        users = users.filter(is_active=False)
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(full_name__icontains=query)
            | Q(mobile_number__icontains=query)
        )
    page_number = request.GET.get("page", "1")
    page_number = int(page_number) if page_number.isdigit() else 1
    page_size = 25
    total = users.count()
    page_count = max(1, (total + page_size - 1) // page_size)
    page_number = min(max(page_number, 1), page_count)
    page_items = users[(page_number - 1) * page_size : page_number * page_size]
    counts = {
        "all": User.objects.count(),
        "admin": User.objects.filter(role=User.Role.ADMIN).count(),
        "staff": User.objects.filter(role=User.Role.STAFF).count(),
        "reporter": User.objects.filter(role=User.Role.REPORTER).count(),
        "active": User.objects.filter(is_active=True).count(),
        "inactive": User.objects.filter(is_active=False).count(),
    }
    context = {
        "users": page_items,
        "page": page_number,
        "page_count": page_count,
        "total": total,
        "counts": counts,
        "roles": User.Role.choices,
        "filters": request.GET,
        "recent_logs": UserManagementLog.objects.select_related("target_user", "performed_by")[:8],
        "active_page": "user-management",
        **nav_counts(),
    }
    return render(request, "dashboard/user_management.html", context)


@admin_required
def user_management_create(request):
    form = DashboardUserForm(request.POST or None, is_create=True)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        UserManagementLog.objects.create(
            target_user=user,
            performed_by=request.user,
            action=UserManagementLog.Action.CREATED,
            summary=f"Created {user.get_role_display()} account {user.username}.",
        )
        messages.success(request, f"User {user.full_name} created.")
        return redirect("dashboard-user-edit", user_id=user.pk)
    return render(
        request,
        "dashboard/user_form.html",
        {
            "form": form,
            "page_title": "Add user",
            "page_eyebrow": "USER MANAGEMENT",
            "submit_label": "Create account",
            "is_create": True,
            "active_page": "user-management",
            **nav_counts(),
        },
    )


@admin_required
def user_management_edit(request, user_id):
    user = get_object_or_404(User.objects.select_related("barangay"), pk=user_id)
    form = DashboardUserForm(request.POST or None, instance=user, is_create=False)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "toggle_active":
            if user.pk == request.user.pk:
                messages.error(request, "You cannot deactivate your own account.")
                return redirect("dashboard-user-edit", user_id=user.pk)
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            log_action = (
                UserManagementLog.Action.ACTIVATED if user.is_active else UserManagementLog.Action.DEACTIVATED
            )
            UserManagementLog.objects.create(
                target_user=user,
                performed_by=request.user,
                action=log_action,
                summary=f"Marked {user.username} as {'active' if user.is_active else 'inactive'}.",
            )
            messages.success(request, f"{user.full_name} is now {'active' if user.is_active else 'inactive'}.")
            return redirect("dashboard-user-edit", user_id=user.pk)
        if form.is_valid():
            previous_role = user.role
            password_changed = bool(form.cleaned_data.get("password"))
            updated = form.save()
            summary_parts = [f"Updated profile for {updated.username}."]
            if previous_role != updated.role:
                summary_parts.append(f"Role changed to {updated.get_role_display()}.")
            if password_changed:
                UserManagementLog.objects.create(
                    target_user=updated,
                    performed_by=request.user,
                    action=UserManagementLog.Action.PASSWORD_RESET,
                    summary=f"Password reset for {updated.username}.",
                )
            UserManagementLog.objects.create(
                target_user=updated,
                performed_by=request.user,
                action=UserManagementLog.Action.UPDATED,
                summary=" ".join(summary_parts),
            )
            messages.success(request, f"Saved changes for {updated.full_name}.")
            return redirect("dashboard-user-edit", user_id=updated.pk)
    logs = UserManagementLog.objects.filter(target_user=user).select_related("performed_by")[:20]
    return render(
        request,
        "dashboard/user_form.html",
        {
            "form": form,
            "managed_user": user,
            "logs": logs,
            "page_title": user.full_name,
            "page_eyebrow": "USER MANAGEMENT",
            "submit_label": "Save changes",
            "is_create": False,
            "active_page": "user-management",
            **nav_counts(),
        },
    )


@staff_required
@require_POST
def staff_logout(request):
    logout(request)
    return redirect("staff-login")
