from django.conf import settings
from django.db import models
from django.utils import timezone


class Barangay(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "barangays"

    def __str__(self):
        return self.name


class Hotline(models.Model):
    name = models.CharField(max_length=120)
    number = models.CharField(max_length=40)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "name")

    def __str__(self):
        return f"{self.name}: {self.number}"


class IncidentReport(models.Model):
    class Category(models.TextChoices):
        FIRE = "fire", "Fire"
        CRIME = "crime", "Crime"
        MEDICAL = "medical", "Medical"
        ACCIDENT = "accident", "Accident"
        DISASTER = "disaster", "Disaster"

    class Injured(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        UNKNOWN = "unknown", "Not sure"

    class VerificationAction(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        RELABELED = "relabeled", "Relabeled"
        INVALID = "invalid", "Invalid"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        NEW = "new", "New"
        RECEIVED = "received", "Received"
        VERIFIED = "verified", "Verified"
        RESPONDED = "responded", "Responded"
        CLOSED = "closed", "Closed"
        INVALID = "invalid", "Invalid"

    ref_no = models.CharField(max_length=24, unique=True, blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reports")
    image = models.ImageField(upload_to="reports/%Y/%m/")
    image_width = models.PositiveIntegerField(default=0)
    image_height = models.PositiveIntegerField(default=0)
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    predicted_category = models.CharField(max_length=12, choices=Category.choices)
    confidence = models.FloatField(default=0)
    probabilities = models.JSONField(default=dict)
    model_version = models.ForeignKey(
        "classifier.ModelVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports"
    )
    processing_time_ms = models.FloatField(default=0)
    client_roundtrip_ms = models.FloatField(null=True, blank=True)
    is_low_confidence = models.BooleanField(default=False)
    is_demo_prediction = models.BooleanField(default=False)
    reporter_category = models.CharField(max_length=12, choices=Category.choices, null=True, blank=True)
    verified_category = models.CharField(max_length=12, choices=Category.choices, null=True, blank=True)
    verification_action = models.CharField(max_length=12, choices=VerificationAction.choices, null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="verified_reports"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    barangay = models.ForeignKey(Barangay, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    landmark = models.CharField(max_length=220, blank=True)
    description = models.TextField(blank=True)
    injured = models.CharField(max_length=8, choices=Injured.choices, default=Injured.UNKNOWN)
    contact_number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "created_at")),
            models.Index(fields=("predicted_category", "created_at")),
        ]

    def save(self, *args, **kwargs):
        if not self.ref_no:
            year = timezone.localtime(timezone.now()).year
            prefix = f"EIC-{year}-"
            sequence = IncidentReport.objects.filter(ref_no__startswith=prefix).count() + 1
            self.ref_no = f"{prefix}{sequence:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.ref_no or "Unnumbered incident"


class StatusLog(models.Model):
    report = models.ForeignKey(IncidentReport, on_delete=models.CASCADE, related_name="status_logs")
    from_status = models.CharField(max_length=10, choices=IncidentReport.Status.choices, blank=True)
    to_status = models.CharField(max_length=10, choices=IncidentReport.Status.choices)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="status_changes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.report.ref_no}: {self.from_status} → {self.to_status}"


class EmergencyRequest(models.Model):
    """Citizen emergency assistance request reviewed by MDRRMO staff."""

    class Category(models.TextChoices):
        FIRE = "fire", "Fire"
        CRIME = "crime", "Crime"
        MEDICAL = "medical", "Medical"
        ACCIDENT = "accident", "Accident"
        DISASTER = "disaster", "Disaster"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    ref_no = models.CharField(max_length=24, unique=True, blank=True)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_requests",
    )
    reporter_name = models.CharField(max_length=160, blank=True)
    category = models.CharField(max_length=12, choices=Category.choices, default=Category.OTHER)
    image = models.ImageField(upload_to="emergency_requests/%Y/%m/", blank=True)
    barangay = models.ForeignKey(
        Barangay, null=True, blank=True, on_delete=models.SET_NULL, related_name="emergency_requests"
    )
    landmark = models.CharField(max_length=220, blank=True)
    description = models.TextField()
    contact_number = models.CharField(max_length=40, blank=True)
    incident_report = models.ForeignKey(
        IncidentReport,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emergency_requests",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_emergency_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "created_at")),
        ]

    def save(self, *args, **kwargs):
        if not self.ref_no:
            year = timezone.localtime(timezone.now()).year
            prefix = f"ERQ-{year}-"
            sequence = EmergencyRequest.objects.filter(ref_no__startswith=prefix).count() + 1
            self.ref_no = f"{prefix}{sequence:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.ref_no or "Unnumbered emergency request"


class IncidentMapPin(models.Model):
    """Map location for an incident report (Google Maps marker)."""

    report = models.OneToOneField(IncidentReport, on_delete=models.CASCADE, related_name="map_pin")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    label = models.CharField(max_length=220, blank=True)
    notes = models.TextField(blank=True)
    mapped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="mapped_incidents",
    )
    mapped_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-mapped_at",)
        indexes = [
            models.Index(fields=("latitude", "longitude")),
        ]

    def __str__(self):
        return f"{self.report.ref_no} @ {self.latitude}, {self.longitude}"
