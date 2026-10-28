import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from PIL import Image

from accounts.models import User
from classifier.models import ModelVersion
from incidents.models import Barangay, IncidentReport


def test_image():
    stream = io.BytesIO()
    Image.new("RGB", (48, 32), "#c04b3f").save(stream, format="PNG")
    return SimpleUploadedFile("incident.png", stream.getvalue(), content_type="image/png")


class ReporterApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.barangay = Barangay.objects.create(name="Dunglayan")
        self.reporter = User.objects.create_user(
            mobile_number="09170000021",
            password="correct-horse",
            full_name="Reporter One",
            barangay=self.barangay,
        )
        self.other = User.objects.create_user(
            mobile_number="09170000022",
            password="correct-horse",
            full_name="Reporter Two",
            barangay=self.barangay,
        )
        self.token = Token.objects.create(user=self.reporter)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_public_lookups_and_health(self):
        self.client.credentials()
        self.assertEqual(self.client.get("/api/v1/barangays/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/hotlines/").status_code, 200)
        self.assertEqual(len(self.client.get("/api/v1/categories/").data), 5)
        health = self.client.get("/api/v1/healthz/")
        self.assertEqual(health.json()["status"], "ok")

    def test_registration_requires_privacy_consent(self):
        self.client.credentials()
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "full_name": "New User",
                "mobile_number": "09170000023",
                "barangay_id": self.barangay.id,
                "password": "correct-horse",
                "privacy_consent": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(mobile_number="09170000023").exists())

    def test_register_login_and_staff_role_restriction(self):
        self.client.credentials()
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "full_name": "New User",
                "mobile_number": "09170000023",
                "barangay_id": self.barangay.id,
                "password": "correct-horse",
                "privacy_consent": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.get(mobile_number="09170000023").privacy_consent_at)
        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"mobile_number": self.reporter.mobile_number, "password": "correct-horse"},
            format="json",
        )
        self.assertEqual(login_response.status_code, 200)
        staff = User.objects.create_user(
            mobile_number="09170000024", password="correct-horse", full_name="Staff", role=User.Role.STAFF
        )
        blocked = self.client.post(
            "/api/v1/auth/login/",
            {"mobile_number": staff.mobile_number, "password": "correct-horse"},
            format="json",
        )
        self.assertEqual(blocked.status_code, 403)

    def test_profile_and_push_token(self):
        self.assertEqual(self.client.get("/api/v1/me/").status_code, 200)
        updated = self.client.patch("/api/v1/me/", {"full_name": "Updated Name"}, format="json")
        self.assertEqual(updated.data["full_name"], "Updated Name")
        saved = self.client.post("/api/v1/me/push-token/", {"expo_push_token": "ExponentPushToken[test]"}, format="json")
        self.assertEqual(saved.status_code, 200)
        self.reporter.refresh_from_db()
        self.assertEqual(self.reporter.expo_push_token, "ExponentPushToken[test]")

    def create_report(self, user=None):
        return IncidentReport.objects.create(
            reporter=user or self.reporter,
            image=test_image(),
            image_width=48,
            image_height=32,
            file_size_bytes=100,
            predicted_category="fire",
            confidence=0.72,
            probabilities={"fire": 0.72, "crime": 0.07, "medical": 0.07, "accident": 0.07, "disaster": 0.07},
        )

    def test_classify_creates_demo_draft_and_requires_image(self):
        missing = self.client.post("/api/v1/classify/", {})
        self.assertEqual(missing.status_code, 400)
        response = self.client.post("/api/v1/classify/", {"image": test_image()}, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["is_demo"])
        report = IncidentReport.objects.get(ref_no=response.data["ref_no"])
        self.assertEqual(report.status, IncidentReport.Status.DRAFT)

    def test_report_draft_send_detail_list_and_owner_permissions(self):
        report = self.create_report()
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.other).key}")
        self.assertEqual(other_client.get(f"/api/v1/reports/{report.ref_no}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/reports/{report.ref_no}/").status_code, 404)
        draft = self.client.patch(
            f"/api/v1/reports/{report.ref_no}/",
            {"barangay_id": self.barangay.id, "landmark": "Town plaza", "contact_number": self.reporter.mobile_number},
            format="json",
        )
        self.assertEqual(draft.status_code, 200)
        sent = self.client.post(f"/api/v1/reports/{report.ref_no}/send/")
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.data["status"], IncidentReport.Status.NEW)
        self.assertEqual(self.client.get("/api/v1/reports/").data["count"], 1)
        self.assertEqual(self.client.get(f"/api/v1/reports/{report.ref_no}/").status_code, 200)

    def test_owner_can_discard_draft_and_read_secured_image(self):
        report = self.create_report()
        self.assertEqual(self.client.get(f"/api/v1/reports/{report.ref_no}/image/").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/v1/reports/{report.ref_no}/").status_code, 204)
        self.assertFalse(IncidentReport.objects.filter(pk=report.pk).exists())


class AdminEvaluationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            mobile_number="09170000031", password="admin-password", full_name="EIC Admin"
        )
        self.token = Token.objects.create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.model = ModelVersion.objects.create(
            name="Test model",
            architecture=ModelVersion.Architecture.EFFICIENTNET,
            version="1",
            class_names=["fire", "crime", "medical", "accident", "disaster"],
            is_active=True,
        )

    def test_admin_can_run_evaluation_and_finish(self):
        created = self.client.post("/api/v1/eval/runs/", {"dataset_name": "API test"}, format="json")
        self.assertEqual(created.status_code, 201)
        run_id = created.data["id"]
        self.assertEqual(self.client.get(f"/api/v1/eval/runs/{run_id}/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/eval/runs/{run_id}/classify/", {}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/v1/eval/runs/{run_id}/finish/").status_code, 200)

    def test_reporter_cannot_start_evaluation(self):
        reporter = User.objects.create_user(
            mobile_number="09170000032", password="reporter-password", full_name="Reporter"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=reporter).key}")
        self.assertEqual(self.client.post("/api/v1/eval/runs/", {"dataset_name": "blocked"}).status_code, 403)
