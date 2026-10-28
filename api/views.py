import logging
from pathlib import Path

from django.contrib.auth import authenticate
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from classifier.services import ImageValidationError, classify_image
from evaluation.metrics import finish_test_run
from evaluation.models import TestRecord, TestRun
from incidents.models import Barangay, EmergencyRequest, Hotline, IncidentReport, StatusLog

from .permissions import IsAdminRole, IsMobileAccount
from .serializers import (
    BarangaySerializer,
    EmergencyRequestCreateSerializer,
    EmergencyRequestSerializer,
    HotlineSerializer,
    IncidentDraftSerializer,
    IncidentReportSerializer,
    ProfilePatchSerializer,
    RegisterSerializer,
    UserSerializer,
    probability_rows,
)
from .throttles import ClassifyRateThrottle, LoginRateThrottle

logger = logging.getLogger(__name__)


def error_response(message, field="detail", status_code=status.HTTP_400_BAD_REQUEST):
    return Response({field: [message]} if field != "detail" else {"detail": message}, status=status_code)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data["privacy_consent"]:
            return error_response("Consent is required to create an account.", "privacy_consent")
        from django.utils import timezone

        user = User.objects.create_user(
            mobile_number=data["mobile_number"],
            password=data["password"],
            full_name=data["full_name"],
            last_name=data.get("last_name", ""),
            first_name=data.get("first_name", ""),
            middle_name=data.get("middle_name", ""),
            barangay_id=data["barangay_id"],
            privacy_consent_at=timezone.now(),
            role=User.Role.REPORTER,
        )
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data}, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        mobile_number = str(request.data.get("mobile_number", "")).strip()
        password = request.data.get("password", "")
        if not mobile_number or not password:
            return error_response("Enter your mobile number and password.")
        user = authenticate(request, username=mobile_number, password=password)
        if not user or not user.is_active:
            return error_response("Mobile number or password is incorrect.", status_code=status.HTTP_401_UNAUTHORIZED)
        if user.role not in {User.Role.REPORTER, User.Role.ADMIN}:
            return error_response("Staff accounts use the MDRRMO website.", status_code=status.HTTP_403_FORBIDDEN)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfilePatchSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class PushTokenView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def post(self, request):
        token = serializers.CharField(max_length=255).run_validation(request.data.get("expo_push_token"))
        request.user.expo_push_token = token
        request.user.save(update_fields=["expo_push_token"])
        return Response({"saved": True})


class BarangayListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        qs = Barangay.objects.filter(is_active=True).order_by("name")
        return Response(BarangaySerializer(qs, many=True).data)


class HotlineListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(HotlineSerializer(Hotline.objects.filter(is_active=True), many=True).data)


class CategoryListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        descriptions = {
            "fire": "Fire, smoke, or an active fire response.",
            "crime": "A crime, safety threat, or public security incident.",
            "medical": "A medical emergency or urgent health concern.",
            "accident": "A road, transport, or other accident.",
            "disaster": "Flooding, severe weather, or another disaster.",
        }
        return Response(
            [
                {"key": key, "label": label, "description": descriptions[key]}
                for key, label in IncidentReport.Category.choices
            ]
        )


class ClassifyView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]
    throttle_classes = [ClassifyRateThrottle]

    def post(self, request):
        uploaded = request.FILES.get("image")
        if not uploaded:
            return error_response("Choose an image to classify.", "image")
        try:
            result = classify_image(uploaded)
        except ImageValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            return error_response(message, "image")
        report = IncidentReport.objects.create(
            reporter=request.user,
            image=uploaded,
            image_width=result["image_width"],
            image_height=result["image_height"],
            file_size_bytes=result["file_size_bytes"],
            predicted_category=result["category"],
            confidence=result["confidence"],
            probabilities=result["probabilities"],
            model_version_id=result["model_version_id"],
            processing_time_ms=result["processing_time_ms"],
            is_low_confidence=result["is_low_confidence"],
            is_demo_prediction=result["is_demo"],
            contact_number=request.user.mobile_number,
        )
        return Response(
            {
                "ref_no": report.ref_no,
                "category": report.predicted_category,
                "category_label": report.get_predicted_category_display(),
                "confidence": report.confidence,
                "probabilities": probability_rows(report.probabilities),
                "processing_time_ms": report.processing_time_ms,
                "is_low_confidence": report.is_low_confidence,
                "is_demo": report.is_demo_prediction,
                "image_url": request.build_absolute_uri(f"/api/v1/reports/{report.ref_no}/image/"),
                "image_width": report.image_width,
                "image_height": report.image_height,
                "file_size_bytes": report.file_size_bytes,
            },
            status=status.HTTP_201_CREATED,
        )


class ReportListView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def get(self, request):
        reports = IncidentReport.objects.filter(reporter=request.user).exclude(status=IncidentReport.Status.DRAFT)
        group = request.query_params.get("group", "")
        if group == "pending":
            reports = reports.filter(status__in=[IncidentReport.Status.NEW, IncidentReport.Status.RECEIVED])
        elif group == "verified":
            reports = reports.filter(status__in=[IncidentReport.Status.VERIFIED, IncidentReport.Status.RESPONDED])
        elif group == "closed":
            reports = reports.filter(status__in=[IncidentReport.Status.CLOSED, IncidentReport.Status.INVALID])
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(reports, request, view=self)
        return paginator.get_paginated_response(IncidentReportSerializer(page, many=True, context={"request": request}).data)


def owned_draft(request, ref_no):
    return IncidentReport.objects.filter(
        ref_no=ref_no, reporter=request.user, status=IncidentReport.Status.DRAFT
    ).first()


class ReportDraftView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def patch(self, request, ref_no):
        report = owned_draft(request, ref_no)
        if not report:
            return error_response("Draft report not found.", status_code=status.HTTP_404_NOT_FOUND)
        serializer = IncidentDraftSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(report, key, value)
        report.save()
        return Response(IncidentReportSerializer(report, context={"request": request}).data)

    def delete(self, request, ref_no):
        report = owned_draft(request, ref_no)
        if not report:
            return error_response("Draft report not found.", status_code=status.HTTP_404_NOT_FOUND)
        report.image.delete(save=False)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReportSendView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    @transaction.atomic
    def post(self, request, ref_no):
        report = owned_draft(request, ref_no)
        if not report:
            return error_response("Draft report not found.", status_code=status.HTTP_404_NOT_FOUND)
        if not report.barangay_id:
            return error_response("Choose a barangay before sending.", "barangay_id")
        if not report.contact_number.strip():
            return error_response("Enter a contact number before sending.", "contact_number")
        old_status = report.status
        report.status = IncidentReport.Status.NEW
        report.sent_at = timezone.now()
        report.save(update_fields=["status", "sent_at"])
        StatusLog.objects.create(report=report, from_status=old_status, to_status=report.status, note="Report submitted.")
        return Response(IncidentReportSerializer(report, context={"request": request}).data, status=status.HTTP_200_OK)


class ReportDetailView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def get(self, request, ref_no):
        report = IncidentReport.objects.filter(ref_no=ref_no, reporter=request.user).exclude(
            status=IncidentReport.Status.DRAFT
        ).first()
        if not report:
            return error_response("Report not found.", status_code=status.HTTP_404_NOT_FOUND)
        data = IncidentReportSerializer(report, context={"request": request}).data
        data["timeline"] = [
            {
                "status": log.to_status,
                "label": log.get_to_status_display(),
                "note": log.note,
                "created_at": log.created_at,
            }
            for log in report.status_logs.all()
        ]
        return Response(data)

    def patch(self, request, ref_no):
        report = owned_draft(request, ref_no)
        if not report:
            return error_response("Draft report not found.", status_code=status.HTTP_404_NOT_FOUND)
        serializer = IncidentDraftSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(report, key, value)
        report.save()
        return Response(IncidentReportSerializer(report, context={"request": request}).data)

    def delete(self, request, ref_no):
        report = owned_draft(request, ref_no)
        if not report:
            return error_response("Draft report not found.", status_code=status.HTTP_404_NOT_FOUND)
        report.image.delete(save=False)
        report.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EmergencyRequestListCreateView(APIView):
    """List requires sign-in; create allows guest reports without an account."""

    def get_permissions(self):
        if self.request.method == "POST":
            return [AllowAny()]
        return [IsAuthenticated(), IsMobileAccount()]

    def get(self, request):
        # Include guest submissions that used this account's mobile number.
        requests_qs = EmergencyRequest.objects.filter(
            Q(requester=request.user) | Q(contact_number=request.user.mobile_number)
        ).select_related("barangay", "incident_report").distinct()
        status_filter = request.query_params.get("status", "")
        if status_filter in dict(EmergencyRequest.Status.choices):
            requests_qs = requests_qs.filter(status=status_filter)
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(requests_qs, request, view=self)
        return paginator.get_paginated_response(
            EmergencyRequestSerializer(page, many=True, context={"request": request}).data
        )

    def post(self, request):
        serializer = EmergencyRequestCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        incident = None
        incident_ref = data.pop("incident_ref_no", "") or ""
        authenticated = bool(request.user and request.user.is_authenticated)
        if incident_ref and authenticated:
            incident = IncidentReport.objects.filter(ref_no=incident_ref, reporter=request.user).first()

        contact = data.get("contact_number") or ""
        reporter_name = data.get("reporter_name") or ""
        if authenticated:
            contact = contact or request.user.mobile_number
            reporter_name = reporter_name or request.user.full_name

        emergency = EmergencyRequest.objects.create(
            requester=request.user if authenticated else None,
            reporter_name=reporter_name,
            category=data["category"],
            image=data.get("image"),
            barangay=data.get("barangay"),
            landmark=data.get("landmark", ""),
            description=data["description"],
            contact_number=contact,
            incident_report=incident,
        )
        return Response(
            EmergencyRequestSerializer(emergency, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class EmergencyRequestDetailView(APIView):
    permission_classes = [IsAuthenticated, IsMobileAccount]

    def get(self, request, ref_no):
        emergency = (
            EmergencyRequest.objects.filter(ref_no=ref_no)
            .filter(Q(requester=request.user) | Q(contact_number=request.user.mobile_number))
            .select_related("barangay", "incident_report")
            .first()
        )
        if not emergency:
            return error_response("Emergency request not found.", status_code=status.HTTP_404_NOT_FOUND)
        return Response(EmergencyRequestSerializer(emergency, context={"request": request}).data)


class EmergencyRequestStatusView(APIView):
    """
    Public status poll so the reporter app can mirror MDRRMO approve/decline
    (including guest submissions that have no account).
    """

    permission_classes = [AllowAny]

    def get(self, request, ref_no):
        emergency = (
            EmergencyRequest.objects.filter(ref_no=ref_no)
            .select_related("barangay", "requester")
            .first()
        )
        if not emergency:
            return error_response("Emergency request not found.", status_code=status.HTTP_404_NOT_FOUND)

        contact = str(request.query_params.get("contact_number") or "").strip()
        if contact and emergency.contact_number and contact != emergency.contact_number:
            return error_response("Emergency request not found.", status_code=status.HTTP_404_NOT_FOUND)

        if emergency.reporter_name:
            display_name = emergency.reporter_name
        elif emergency.requester_id:
            display_name = emergency.requester.full_name
        else:
            display_name = "Guest reporter"

        return Response(
            {
                "ref_no": emergency.ref_no,
                "category": emergency.category,
                "category_label": emergency.get_category_display(),
                "barangay": emergency.barangay.name if emergency.barangay_id else None,
                "landmark": emergency.landmark,
                "description": emergency.description,
                "reporter_name": emergency.reporter_name,
                "reporter_display_name": display_name,
                "contact_number": emergency.contact_number,
                "status": emergency.status,
                "status_label": emergency.get_status_display(),
                "review_note": emergency.review_note,
                "created_at": emergency.created_at,
                "reviewed_at": emergency.reviewed_at,
            }
        )


class EvalRunListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        name = serializers.CharField(max_length=180).run_validation(request.data.get("dataset_name"))
        active = __import__("classifier.models", fromlist=["ModelVersion"]).ModelVersion.objects.filter(
            is_active=True
        ).first()
        if not active:
            return error_response("Upload and activate a trained model before starting evaluation.")
        run = TestRun.objects.create(model_version=active, dataset_name=name, source=TestRun.Source.MOBILE_APP)
        return Response({"id": run.id, "dataset_name": run.dataset_name, "status": run.status}, status=201)


class EvalRunClassifyView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, run_id):
        run = TestRun.objects.filter(pk=run_id, source=TestRun.Source.MOBILE_APP, status=TestRun.Status.RUNNING).first()
        if not run:
            return error_response("Evaluation run not found or already finished.", status_code=404)
        uploaded = request.FILES.get("image")
        reference = request.data.get("reference_label")
        if not uploaded:
            return error_response("Choose an image.", "image")
        if reference not in settings.EIC_CATEGORIES:
            return error_response("Choose a valid reference category.", "reference_label")
        try:
            result = classify_image(uploaded, model_version=run.model_version)
        except ImageValidationError as exc:
            message = exc.messages[0] if hasattr(exc, "messages") else str(exc)
            record = TestRecord.objects.create(
                run=run,
                image_name=Path(uploaded.name).name,
                image=uploaded,
                reference_label=reference,
                validation_status=TestRecord.ValidationStatus.FAILED,
                error_message=message,
            )
            run.total_images = run.records.count()
            run.failed = run.records.filter(validation_status=TestRecord.ValidationStatus.FAILED).count()
            run.save(update_fields=["total_images", "failed"])
            return Response({"record_id": record.id, "validation_status": record.validation_status, "error": message}, status=400)
        if result["is_demo"]:
            return error_response("The selected model could not be loaded; demo predictions are not evaluated.", status_code=503)
        record = TestRecord.objects.create(
            run=run,
            image_name=Path(uploaded.name).name,
            image=uploaded,
            reference_label=reference,
            predicted_label=result["category"],
            confidence=result["confidence"],
            is_correct=result["category"] == reference,
            processing_time_ms=result["processing_time_ms"],
            app_displayed_label=result["category"],
        )
        run.total_images = run.records.count()
        run.successful = run.records.filter(validation_status=TestRecord.ValidationStatus.OK).count()
        run.save(update_fields=["total_images", "successful"])
        return Response(
            {
                "record_id": record.id,
                "reference_label": record.reference_label,
                "predicted_label": record.predicted_label,
                "app_displayed_label": record.app_displayed_label,
                "confidence": record.confidence,
                "is_correct": record.is_correct,
                "processing_time_ms": record.processing_time_ms,
            },
            status=201,
        )


class EvalRecordPatchView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def patch(self, request, record_id):
        record = TestRecord.objects.filter(pk=record_id, run__source=TestRun.Source.MOBILE_APP).first()
        if not record:
            return error_response("Evaluation record not found.", status_code=404)
        value = serializers.FloatField(min_value=0, max_value=600000).run_validation(
            request.data.get("client_roundtrip_ms")
        )
        record.client_roundtrip_ms = value
        record.save(update_fields=["client_roundtrip_ms"])
        return Response({"id": record.id, "client_roundtrip_ms": record.client_roundtrip_ms})


class EvalRunDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def get(self, request, run_id):
        run = TestRun.objects.filter(pk=run_id).first()
        if not run:
            return error_response("Evaluation run not found.", status_code=404)
        return Response(
            {
                "id": run.id,
                "dataset_name": run.dataset_name,
                "status": run.status,
                "total_images": run.total_images,
                "successful": run.successful,
                "failed": run.failed,
                "accuracy": run.accuracy,
                "macro_f1": run.macro_f1,
                "mean_ms": run.mean_ms,
                "mean_roundtrip_ms": run.mean_roundtrip_ms,
            }
        )


class EvalRunFinishView(APIView):
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, run_id):
        run = TestRun.objects.filter(pk=run_id, status=TestRun.Status.RUNNING).first()
        if not run:
            return error_response("Evaluation run not found or already finished.", status_code=404)
        finish_test_run(run)
        return EvalRunDetailView().get(request, run_id)
