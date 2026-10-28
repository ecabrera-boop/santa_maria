from django.conf import settings
from django.db import models

from incidents.models import IncidentReport


class TestRun(models.Model):
    class Source(models.TextChoices):
        COMMAND = "command", "Management command"
        MOBILE_APP = "mobile_app", "Mobile app"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    model_version = models.ForeignKey("classifier.ModelVersion", null=True, blank=True, on_delete=models.SET_NULL)
    dataset_name = models.CharField(max_length=180)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.COMMAND)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    total_images = models.PositiveIntegerField(default=0)
    successful = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    accuracy = models.FloatField(null=True, blank=True)
    macro_precision = models.FloatField(null=True, blank=True)
    macro_recall = models.FloatField(null=True, blank=True)
    macro_f1 = models.FloatField(null=True, blank=True)
    weighted_precision = models.FloatField(null=True, blank=True)
    weighted_recall = models.FloatField(null=True, blank=True)
    weighted_f1 = models.FloatField(null=True, blank=True)
    confusion_matrix = models.JSONField(default=list)
    per_class = models.JSONField(default=dict)
    mean_ms = models.FloatField(null=True, blank=True)
    median_ms = models.FloatField(null=True, blank=True)
    min_ms = models.FloatField(null=True, blank=True)
    max_ms = models.FloatField(null=True, blank=True)
    mean_roundtrip_ms = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)

    def __str__(self):
        return f"{self.dataset_name} ({self.started_at:%Y-%m-%d})"


class TestRecord(models.Model):
    class ValidationStatus(models.TextChoices):
        OK = "ok", "Valid"
        FAILED = "failed", "Failed"

    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name="records")
    image_name = models.CharField(max_length=255)
    image = models.ImageField(upload_to="evaluation/%Y/%m/", null=True, blank=True)
    reference_label = models.CharField(max_length=12, choices=IncidentReport.Category.choices)
    predicted_label = models.CharField(max_length=12, choices=IncidentReport.Category.choices, null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    processing_time_ms = models.FloatField(null=True, blank=True)
    client_roundtrip_ms = models.FloatField(null=True, blank=True)
    validation_status = models.CharField(max_length=8, choices=ValidationStatus.choices, default=ValidationStatus.OK)
    error_message = models.TextField(blank=True)
    app_displayed_label = models.CharField(max_length=12, choices=IncidentReport.Category.choices, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.image_name} ({self.reference_label})"
