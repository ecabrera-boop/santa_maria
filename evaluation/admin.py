from django.contrib import admin

from .models import TestRecord, TestRun


class TestRecordInline(admin.TabularInline):
    model = TestRecord
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(TestRun)
class TestRunAdmin(admin.ModelAdmin):
    list_display = ("dataset_name", "model_version", "source", "status", "total_images", "accuracy", "started_at")
    list_filter = ("status", "source", "model_version")
    search_fields = ("dataset_name",)
    inlines = (TestRecordInline,)


@admin.register(TestRecord)
class TestRecordAdmin(admin.ModelAdmin):
    list_display = ("image_name", "run", "reference_label", "predicted_label", "is_correct", "validation_status")
    list_filter = ("validation_status", "reference_label", "predicted_label", "is_correct")
    search_fields = ("image_name", "run__dataset_name")
