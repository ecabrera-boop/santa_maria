from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models


class ModelVersion(models.Model):
    class Architecture(models.TextChoices):
        EFFICIENTNET = "efficientnet", "EfficientNet"
        MOBILENET_V2 = "mobilenet_v2", "MobileNetV2"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120)
    architecture = models.CharField(max_length=20, choices=Architecture.choices, default=Architecture.EFFICIENTNET)
    version = models.CharField(max_length=40)
    model_file = models.FileField(
        upload_to="models/",
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["keras", "tflite"])],
    )
    class_names = models.JSONField(default=list)
    input_size = models.PositiveIntegerField(default=224)
    is_active = models.BooleanField(default=False)
    training_history = models.JSONField(null=True, blank=True)
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-uploaded_at",)
        constraints = [
            models.UniqueConstraint(fields=("name", "version"), name="unique_classifier_model_version"),
        ]

    def clean(self):
        from django.conf import settings

        names = self.class_names if isinstance(self.class_names, list) else []
        if len(names) != len(settings.EIC_CATEGORIES) or set(names) != set(settings.EIC_CATEGORIES):
            raise ValidationError(
                {"class_names": f"Include each category exactly once: {', '.join(settings.EIC_CATEGORIES)}."}
            )
        if self.input_size < 32 or self.input_size > 1024:
            raise ValidationError({"input_size": "Input size must be between 32 and 1024 pixels."})

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.is_active:
            type(self).objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} {self.version}"
