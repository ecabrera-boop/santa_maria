from django.contrib import admin, messages

from .models import ModelVersion


@admin.action(description="Set as active model")
def set_active_model(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Select exactly one model version.", messages.ERROR)
        return
    version = queryset.first()
    version.is_active = True
    version.save()
    modeladmin.message_user(request, f"{version} is now the active model.", messages.SUCCESS)


@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    list_display = ("name", "architecture", "version", "is_active", "uploaded_at")
    list_filter = ("architecture", "is_active")
    search_fields = ("name", "version")
    actions = (set_active_model,)
