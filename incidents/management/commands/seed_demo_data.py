import io
import math
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from accounts.models import User
from incidents.models import Barangay, IncidentMapPin, IncidentReport, StatusLog

# Approximate center of Santa Maria, Ilocos Sur
SANTA_MARIA_LAT = 17.3665
SANTA_MARIA_LNG = 120.4818

SANTA_MARIA_BARANGAYS = [
    "Ag-agrao",
    "Ampuagan",
    "Baballasioan",
    "Baliw Daya",
    "Baliw Laud",
    "Bia-o",
    "Butir",
    "Cabaroan",
    "Danuman East",
    "Danuman West",
    "Dunglayan",
    "Gusing",
    "Langaoan",
    "Laslasong Norte",
    "Laslasong Sur",
    "Laslasong West",
    "Lesseb",
    "Lingsat",
    "Lubong",
    "Maynganay Norte",
    "Maynganay Sur",
    "Nagsayaoan",
    "Nagtupacan",
    "Nalvo",
    "Pacang",
    "Penned",
    "Poblacion Norte",
    "Poblacion Sur",
    "Silag",
    "Sumagui",
    "Suso",
    "Tangaoan",
    "Tinaan",
]

LANDMARKS = [
    "Near Santa Maria Municipal Hall",
    "Along the national highway",
    "Beside Santa Maria Church",
    "Public market area",
    "Elementary school frontage",
    "Barangay health station",
    "Basketball court",
    "Bridge approach",
    "Rice field access road",
    "Coastal road junction",
    "Chapel parking area",
    "Tricycle terminal",
    "Barangay hall driveway",
    "Irrigation canal road",
    "Corner sari-sari store",
]


class Command(BaseCommand):
    help = "Create 30 sample Santa Maria, Ilocos Sur incident reports with map pins."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo reports before seeding.",
        )

    def handle(self, *args, **options):
        reporter = User.objects.filter(role=User.Role.REPORTER).order_by("date_joined").first()
        if not reporter:
            self.stderr.write("Run `python manage.py create_demo_users` first.")
            return

        if options["reset"]:
            deleted, _ = IncidentReport.objects.filter(is_demo_prediction=True).delete()
            self.stdout.write(f"Removed {deleted} existing demo reports.")

        barangays = self._ensure_barangays()
        categories = list(IncidentReport.Category.values)
        statuses = [
            IncidentReport.Status.NEW,
            IncidentReport.Status.RECEIVED,
            IncidentReport.Status.VERIFIED,
            IncidentReport.Status.RESPONDED,
            IncidentReport.Status.CLOSED,
            IncidentReport.Status.INVALID,
        ]
        already = IncidentReport.objects.filter(is_demo_prediction=True).count()
        if already >= 30:
            self._ensure_pins(reporter)
            self.stdout.write(f"Demo data already has {already} reports; map pins refreshed.")
            return

        staff = User.objects.filter(role__in=[User.Role.ADMIN, User.Role.STAFF]).first()
        created = 0
        for index in range(already, 30):
            category = categories[index % len(categories)]
            status = statuses[index % len(statuses)]
            confidence = 0.42 + ((index * 13) % 55) / 100
            probabilities = {key: 0.02 for key in categories}
            probabilities[category] = confidence
            remainder = (1 - confidence) / (len(categories) - 1)
            for key in categories:
                if key != category:
                    probabilities[key] = remainder
            verified_category = categories[(index + (1 if index % 4 == 0 else 0)) % len(categories)]
            is_verified = status in {
                IncidentReport.Status.VERIFIED,
                IncidentReport.Status.RESPONDED,
                IncidentReport.Status.CLOSED,
            }
            reported_at = timezone.now() - timedelta(hours=index * 3)
            barangay = barangays[index % len(barangays)]
            landmark = LANDMARKS[index % len(LANDMARKS)]
            report = IncidentReport(
                reporter=reporter,
                barangay=barangay,
                landmark=landmark,
                description=(
                    f"Sample {category} incident in {barangay.name}, Santa Maria, Ilocos Sur. "
                    "Demonstration data only — not a real emergency."
                ),
                injured=IncidentReport.Injured.NO if index % 3 else IncidentReport.Injured.UNKNOWN,
                contact_number=reporter.mobile_number,
                predicted_category=category,
                confidence=confidence,
                probabilities=probabilities,
                is_demo_prediction=True,
                is_low_confidence=confidence < 0.6,
                status=status,
                verified_category=(
                    verified_category if is_verified and status != IncidentReport.Status.INVALID else None
                ),
                verification_action=(
                    IncidentReport.VerificationAction.INVALID
                    if status == IncidentReport.Status.INVALID
                    else IncidentReport.VerificationAction.RELABELED
                    if is_verified and verified_category != category
                    else IncidentReport.VerificationAction.CONFIRMED
                    if is_verified
                    else None
                ),
                verified_by=staff if is_verified or status == IncidentReport.Status.INVALID else None,
                verified_at=(
                    reported_at + timedelta(hours=1)
                    if is_verified or status == IncidentReport.Status.INVALID
                    else None
                ),
                processing_time_ms=38 + index * 3.8,
                sent_at=reported_at,
            )
            image = Image.new("RGB", (900, 600), "#d7d7d2")
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("arial.ttf", 48)
                small = ImageFont.truetype("arial.ttf", 24)
            except OSError:
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", 48)
                    small = ImageFont.truetype("DejaVuSans.ttf", 24)
                except OSError:
                    font, small = ImageFont.load_default(), ImageFont.load_default()
            draw.rectangle((28, 28, 872, 572), outline="#b42318", width=4)
            draw.text((60, 180), category.upper(), fill="#1c1c1a", font=font)
            draw.text((62, 255), barangay.name.upper(), fill="#55554f", font=small)
            draw.text((62, 300), "SANTA MARIA · ILOCOS SUR", fill="#55554f", font=small)
            draw.text((62, 345), "DEMO IMAGE · NOT A REAL INCIDENT", fill="#8f1c15", font=small)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82)
            report.image.save(f"demo-{category}-{index + 1:02d}.jpg", ContentFile(buffer.getvalue()), save=False)
            report.save()
            IncidentReport.objects.filter(pk=report.pk).update(created_at=reported_at)
            lat, lng = self._coords_for(index)
            IncidentMapPin.objects.update_or_create(
                report=report,
                defaults={
                    "latitude": Decimal(f"{lat:.6f}"),
                    "longitude": Decimal(f"{lng:.6f}"),
                    "label": f"{landmark} · {barangay.name}",
                    "notes": "Seeded Santa Maria demo pin.",
                    "mapped_by": staff,
                },
            )
            if status != IncidentReport.Status.NEW:
                StatusLog.objects.create(
                    report=report,
                    from_status=IncidentReport.Status.NEW,
                    to_status=status,
                    note="Seeded demonstration status. Not a live report.",
                    changed_by=report.verified_by,
                )
            created += 1
        self.stdout.write(self.style.SUCCESS(f"Created {created} Santa Maria sample reports with map pins."))

    def _ensure_barangays(self):
        official = set(SANTA_MARIA_BARANGAYS)
        barangays = []
        for name in SANTA_MARIA_BARANGAYS:
            barangay, _ = Barangay.objects.get_or_create(name=name, defaults={"is_active": True})
            if not barangay.is_active:
                barangay.is_active = True
                barangay.save(update_fields=["is_active"])
            barangays.append(barangay)
        Barangay.objects.exclude(name__in=official).filter(is_active=True).update(is_active=False)
        return barangays

    def _coords_for(self, index):
        # Spread pins in a ring around Santa Maria town center (~0.5–3 km).
        angle = (index / 30) * 2 * math.pi
        radius = 0.004 + (index % 7) * 0.0022
        lat = SANTA_MARIA_LAT + math.cos(angle) * radius
        lng = SANTA_MARIA_LNG + math.sin(angle) * radius * 1.05
        return lat, lng

    def _ensure_pins(self, reporter):
        staff = User.objects.filter(role__in=[User.Role.ADMIN, User.Role.STAFF]).first()
        reports = list(
            IncidentReport.objects.filter(is_demo_prediction=True)
            .select_related("barangay", "map_pin")
            .order_by("pk")
        )
        for index, report in enumerate(reports):
            lat, lng = self._coords_for(index)
            IncidentMapPin.objects.update_or_create(
                report=report,
                defaults={
                    "latitude": Decimal(f"{lat:.6f}"),
                    "longitude": Decimal(f"{lng:.6f}"),
                    "label": f"{report.landmark or report.ref_no} · {report.barangay.name if report.barangay_id else 'Santa Maria'}",
                    "notes": "Seeded Santa Maria demo pin.",
                    "mapped_by": staff,
                },
            )
