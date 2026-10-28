from django.contrib import admin

from .models import Barangay, EmergencyRequest, Hotline, IncidentMapPin, IncidentReport, StatusLog


@admin.register(Barangay)
class BarangayAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Hotline)
class HotlineAdmin(admin.ModelAdmin):
    list_display = ("name", "number", "order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "number")


@admin.register(IncidentReport)
class IncidentReportAdmin(admin.ModelAdmin):
    list_display = (
        "ref_no", "predicted_category", "confidence", "verified_category", "barangay", "status",
        "is_low_confidence", "is_demo_prediction", "created_at",
    )
    list_filter = ("status", "predicted_category", "verified_category", "barangay", "is_low_confidence")
    search_fields = ("ref_no", "reporter__full_name", "reporter__mobile_number", "landmark")
    readonly_fields = ("ref_no", "created_at", "sent_at")


@admin.register(StatusLog)
class StatusLogAdmin(admin.ModelAdmin):
    list_display = ("report", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("to_status",)
    search_fields = ("report__ref_no",)
    readonly_fields = ("created_at",)


@admin.register(EmergencyRequest)
class EmergencyRequestAdmin(admin.ModelAdmin):
    list_display = ("ref_no", "category", "barangay", "requester", "status", "reviewed_by", "created_at")
    list_filter = ("status", "category", "barangay")
    search_fields = ("ref_no", "requester__full_name", "requester__mobile_number", "landmark", "description")
    readonly_fields = ("ref_no", "created_at", "reviewed_at")
    fields = (
        "ref_no",
        "requester",
        "reporter_name",
        "category",
        "image",
        "barangay",
        "landmark",
        "description",
        "contact_number",
        "incident_report",
        "status",
        "review_note",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )


@admin.register(IncidentMapPin)
class IncidentMapPinAdmin(admin.ModelAdmin):
    list_display = ("report", "latitude", "longitude", "label", "mapped_by", "mapped_at")
    search_fields = ("report__ref_no", "label", "notes")
    readonly_fields = ("created_at", "mapped_at")
    raw_id_fields = ("report",)
