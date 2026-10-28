import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from accounts.models import User
from incidents.models import Barangay, IncidentReport


def sample_image():
    stream = io.BytesIO()
    Image.new("RGB", (64, 40), "#b42318").save(stream, format="JPEG")
    return SimpleUploadedFile("report.jpg", stream.getvalue(), content_type="image/jpeg")


class StaffDashboardTests(TestCase):
    def setUp(self):
        self.barangay = Barangay.objects.create(name="Dunglayan")
        self.staff = User.objects.create_user(
            mobile_number="09170000041",
            password="staff-password",
            full_name="MDRRMO Staff",
            role=User.Role.STAFF,
        )
        self.report = IncidentReport.objects.create(
            reporter=User.objects.create_user(
                mobile_number="09170000042",
                password="reporter-password",
                full_name="Reporter",
                barangay=self.barangay,
            ),
            image=sample_image(),
            image_width=64,
            image_height=40,
            file_size_bytes=500,
            predicted_category=IncidentReport.Category.FIRE,
            confidence=0.72,
            probabilities={"fire": 0.72, "crime": 0.07, "medical": 0.07, "accident": 0.07, "disaster": 0.07},
            barangay=self.barangay,
            status=IncidentReport.Status.NEW,
        )

    def test_staff_pages_render_and_dashboard_requires_staff(self):
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)
        self.assertEqual(self.client.get("/dashboard/incidents/").status_code, 200)
        self.assertEqual(self.client.get("/dashboard/system-templates/").status_code, 200)
        home = self.client.get("/dashboard/")
        self.assertContains(home, "System templates")
        self.assertNotContains(home, "Model evaluation")
        self.assertNotContains(home, "Test logs")

    def test_staff_can_save_system_templates(self):
        self.client.force_login(self.staff)
        response = self.client.get("/dashboard/system-templates/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Received by MDRRMO.")
        save = self.client.post(
            "/dashboard/system-templates/",
            {
                "title_received": "Report received",
                "body_received": "MDRRMO received your report {ref_no}.",
                "title_verified": "Report verified",
                "body_verified": "Verified as {category} by MDRRMO.",
                "title_invalid": "Report invalid",
                "body_invalid": "MDRRMO marked this report as invalid.",
                "title_status_updated": "Status update",
                "body_status_updated": "Your report is now {status}.",
            },
        )
        self.assertEqual(save.status_code, 302)
        from incidents.models import SystemTemplate

        template = SystemTemplate.objects.get(key=SystemTemplate.Key.RECEIVED)
        self.assertEqual(template.body, "MDRRMO received your report {ref_no}.")

    def test_staff_can_login_with_username(self):
        self.staff.username = "mdrrmo_staff"
        self.staff.save(update_fields=["username"])
        response = self.client.post("/login/", {"username": "mdrrmo_staff", "password": "staff-password"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")

    def test_opening_new_report_receives_and_logs_it(self):
        self.client.force_login(self.staff)
        response = self.client.get(f"/dashboard/incidents/{self.report.ref_no}/")
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, IncidentReport.Status.RECEIVED)
        self.assertTrue(self.report.status_logs.filter(to_status=IncidentReport.Status.RECEIVED).exists())

    def test_staff_can_verify_and_relabel(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            f"/dashboard/incidents/{self.report.ref_no}/",
            {"action": "relabel", "verified_category": IncidentReport.Category.ACCIDENT, "note": "Road collision"},
        )
        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.verified_category, IncidentReport.Category.ACCIDENT)
        self.assertEqual(self.report.verification_action, IncidentReport.VerificationAction.RELABELED)
        self.assertEqual(self.report.verified_by, self.staff)

    def test_staff_can_export_filtered_report_list(self):
        self.client.force_login(self.staff)
        response = self.client.get("/dashboard/incidents/?category=fire&export=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
