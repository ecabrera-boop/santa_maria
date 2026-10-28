import mimetypes
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from classifier.models import ModelVersion
from classifier.services import ImageValidationError, classify_image
from evaluation.metrics import finish_test_run
from evaluation.models import TestRecord, TestRun


class Command(BaseCommand):
    help = "Evaluate an active classifier against a folder tree labeled by category."

    def add_arguments(self, parser):
        parser.add_argument("--test-dir", required=True)
        parser.add_argument("--model-version", type=int)
        parser.add_argument("--dataset-name", default="Held-out test set")

    def handle(self, *args, **options):
        root = Path(options["test_dir"]).expanduser().resolve()
        if not root.is_dir():
            raise CommandError(f"Test directory does not exist: {root}")
        version = ModelVersion.objects.filter(pk=options["model_version"]).first() if options["model_version"] else ModelVersion.objects.filter(is_active=True).first()
        if not version:
            raise CommandError("No trained model is active. Demo predictions cannot be evaluated.")
        if not version.model_file:
            raise CommandError("The selected model version has no model file.")
        from django.conf import settings

        missing = [label for label in settings.EIC_CATEGORIES if not (root / label).is_dir()]
        if missing:
            raise CommandError(f"Missing category folders: {', '.join(missing)}.")
        run = TestRun.objects.create(model_version=version, dataset_name=options["dataset_name"], source="command")
        image_count = 0
        try:
            for label in settings.EIC_CATEGORIES:
                folder = root / label
                if not folder.is_dir():
                    continue
                for path in sorted(item for item in folder.rglob("*") if item.is_file()):
                    image_count += 1
                    mime, _ = mimetypes.guess_type(path.name)
                    mime = mime or "application/octet-stream"
                    try:
                        payload = SimpleUploadedFile(path.name, path.read_bytes(), content_type=mime)
                        result = classify_image(payload, model_version=version)
                        if result["is_demo"]:
                            raise CommandError("Classifier entered demo mode. Evaluation stopped.")
                        TestRecord.objects.create(
                            run=run,
                            image_name=path.name,
                            image=payload,
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
                            image_name=path.name,
                            image=payload,
                            reference_label=label,
                            validation_status=TestRecord.ValidationStatus.FAILED,
                            error_message=message,
                        )
                    run.total_images = image_count
                    run.successful = run.records.filter(validation_status="ok").count()
                    run.failed = run.records.filter(validation_status="failed").count()
                    run.save(update_fields=["total_images", "successful", "failed"])
            if not image_count:
                raise CommandError("No files found inside category subfolders.")
            finish_test_run(run)
        except Exception:
            run.status = TestRun.Status.FAILED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            raise
        self.stdout.write(
            self.style.SUCCESS(
                f"Evaluation complete: {run.successful} successful, {run.failed} failed, "
                f"accuracy {run.accuracy if run.accuracy is not None else 'n/a'}."
            )
        )
