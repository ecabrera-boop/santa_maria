import io

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.models import User
from classifier.services import ImageValidationError, classify_image
from incidents.models import Barangay, IncidentReport


def image_upload(name="sample.png", size=(32, 24), content_type="image/png"):
    buffer = io.BytesIO()
    Image.new("RGB", size, "#b42318").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


class ClassifierServiceTests(TestCase):
    @override_settings(EIC_MAX_UPLOAD_MB=1)
    def test_rejects_wrong_file_type(self):
        with self.assertRaises(ImageValidationError):
            classify_image(SimpleUploadedFile("sample.txt", b"not an image", content_type="text/plain"))

    @override_settings(EIC_MAX_UPLOAD_MB=0)
    def test_rejects_oversized_file(self):
        with self.assertRaises(ImageValidationError):
            classify_image(image_upload())

    def test_rejects_corrupt_image(self):
        with self.assertRaises(ImageValidationError):
            classify_image(SimpleUploadedFile("bad.png", b"broken", content_type="image/png"))

    def test_demo_mode_is_deterministic_and_has_probabilities(self):
        first = classify_image(image_upload())
        second = classify_image(image_upload())
        self.assertTrue(first["is_demo"])
        self.assertEqual(first["category"], second["category"])
        self.assertAlmostEqual(sum(first["probabilities"].values()), 1.0, places=5)


class ClassifierModelTests(TestCase):
    def setUp(self):
        self.barangay = Barangay.objects.create(name="Dunglayan")
        self.reporter = User.objects.create_user(
            mobile_number="09170000011",
            password="test-password",
            full_name="Test Reporter",
            barangay=self.barangay,
        )

    def test_demo_result_sets_low_confidence_flag_from_config(self):
        report_result = classify_image(image_upload())
        self.assertEqual(report_result["is_low_confidence"], report_result["confidence"] < 0.60)
