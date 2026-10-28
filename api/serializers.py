import base64
import binascii
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from rest_framework import serializers

from accounts.models import User, mobile_number_validator
from incidents.models import Barangay, EmergencyRequest, Hotline, IncidentReport


class UserSerializer(serializers.ModelSerializer):
    barangay_id = serializers.IntegerField(source="barangay.id", read_only=True, allow_null=True)
    barangay = serializers.CharField(source="barangay.name", read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = ("id", "mobile_number", "full_name", "role", "barangay_id", "barangay")


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    middle_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    mobile_number = serializers.CharField(max_length=11, validators=[mobile_number_validator])
    barangay_id = serializers.IntegerField()
    password = serializers.CharField(min_length=8, write_only=True, trim_whitespace=False)
    privacy_consent = serializers.BooleanField()

    def validate_mobile_number(self, value):
        if User.objects.filter(mobile_number=value).exists():
            raise serializers.ValidationError("An account with this mobile number already exists.")
        return value

    def validate_barangay_id(self, value):
        if not Barangay.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError("Select an active barangay.")
        return value

    def validate_privacy_consent(self, value):
        if value is not True:
            raise serializers.ValidationError("Consent is required to create an account.")
        return value

    def validate(self, attrs):
        last_name = User.normalize_name_part(attrs.get("last_name", ""))
        first_name = User.normalize_name_part(attrs.get("first_name", ""))
        middle_name = User.normalize_name_part(attrs.get("middle_name", ""))
        if last_name or first_name:
            if not last_name:
                raise serializers.ValidationError({"last_name": "Last name is required."})
            if not first_name:
                raise serializers.ValidationError({"first_name": "First name is required."})
            attrs["last_name"] = last_name
            attrs["first_name"] = first_name
            attrs["middle_name"] = middle_name
            attrs["full_name"] = User.compose_full_name(last_name, first_name, middle_name)
        else:
            full_name = User.normalize_name_part(attrs.get("full_name", ""))
            if not full_name:
                raise serializers.ValidationError({"full_name": "Full name is required."})
            attrs["full_name"] = full_name
            attrs["last_name"] = ""
            attrs["first_name"] = ""
            attrs["middle_name"] = ""
        return attrs


class ProfilePatchSerializer(serializers.ModelSerializer):
    barangay_id = serializers.PrimaryKeyRelatedField(
        source="barangay", queryset=Barangay.objects.filter(is_active=True), required=False, allow_null=True
    )
    mobile_number = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ("full_name", "mobile_number", "barangay_id")


class BarangaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Barangay
        fields = ("id", "name")


class HotlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hotline
        fields = ("id", "name", "number")


class IncidentDraftSerializer(serializers.Serializer):
    reporter_category = serializers.ChoiceField(choices=IncidentReport.Category.choices, required=False, allow_null=True)
    barangay_id = serializers.PrimaryKeyRelatedField(
        source="barangay", queryset=Barangay.objects.filter(is_active=True), required=False, allow_null=True
    )
    landmark = serializers.CharField(max_length=220, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    injured = serializers.ChoiceField(choices=IncidentReport.Injured.choices, required=False)
    contact_number = serializers.CharField(max_length=40, required=False, allow_blank=True)
    client_roundtrip_ms = serializers.FloatField(required=False, min_value=0, max_value=600000)


class IncidentReportSerializer(serializers.ModelSerializer):
    category = serializers.CharField(source="predicted_category")
    category_label = serializers.CharField(source="get_predicted_category_display")
    barangay_id = serializers.IntegerField(source="barangay.id", read_only=True, allow_null=True)
    barangay = serializers.CharField(source="barangay.name", read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField()
    model_version = serializers.CharField(source="model_version.name", read_only=True, allow_null=True)
    verified_category_label = serializers.CharField(source="get_verified_category_display", read_only=True)

    class Meta:
        model = IncidentReport
        fields = (
            "ref_no", "category", "category_label", "confidence", "probabilities", "processing_time_ms",
            "client_roundtrip_ms", "is_low_confidence", "is_demo_prediction", "image_url", "image_width",
            "image_height", "file_size_bytes", "reporter_category", "verified_category",
            "verified_category_label", "verification_action", "barangay_id", "barangay", "landmark",
            "description", "injured", "contact_number", "status", "created_at", "sent_at", "model_version",
        )

    def get_image_url(self, report):
        request = self.context.get("request")
        if not request:
            return None
        from django.urls import reverse

        path = reverse("api-report-image", kwargs={"ref_no": report.ref_no})
        return request.build_absolute_uri(path)


def probability_rows(probabilities):
    labels = settings.EIC_CATEGORY_LABELS
    return [
        {"category": key, "label": labels.get(key, key.title()), "probability": value}
        for key, value in sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    ]


class EmergencyRequestCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=EmergencyRequest.Category.choices)
    image = serializers.ImageField(required=False, allow_null=True)
    # Mobile-friendly alternative to multipart (avoids Expo FormDataPart errors).
    image_base64 = serializers.CharField(required=False, allow_blank=True, write_only=True)
    reporter_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    barangay_id = serializers.PrimaryKeyRelatedField(
        source="barangay", queryset=Barangay.objects.filter(is_active=True), required=False, allow_null=True
    )
    landmark = serializers.CharField(max_length=220, required=False, allow_blank=True)
    description = serializers.CharField()
    contact_number = serializers.CharField(max_length=40, required=False, allow_blank=True)
    incident_ref_no = serializers.CharField(required=False, allow_blank=True)

    def validate_image_base64(self, value):
        raw = (value or "").strip()
        if not raw:
            return ""
        if "," in raw and raw.lower().startswith("data:"):
            raw = raw.split(",", 1)[1]
        try:
            decoded = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise serializers.ValidationError("Invalid photo data.") from exc
        max_bytes = settings.EIC_MAX_UPLOAD_MB * 1024 * 1024
        if len(decoded) > max_bytes:
            raise serializers.ValidationError(
                f"Image must be smaller than {settings.EIC_MAX_UPLOAD_MB} MB."
            )
        if len(decoded) < 32:
            raise serializers.ValidationError("Photo data is empty.")
        return ContentFile(decoded, name=f"incident-{uuid.uuid4().hex[:10]}.jpg")

    def validate(self, attrs):
        request = self.context["request"]
        is_authenticated = bool(request.user and request.user.is_authenticated)
        contact = (attrs.get("contact_number") or "").strip()
        reporter_name = (attrs.get("reporter_name") or "").strip()
        landmark = (attrs.get("landmark") or "").strip()
        image_from_b64 = attrs.pop("image_base64", None)
        if image_from_b64 and not attrs.get("image"):
            attrs["image"] = image_from_b64
        if not is_authenticated:
            if not reporter_name:
                raise serializers.ValidationError({"reporter_name": "Your name is required."})
            if not contact:
                raise serializers.ValidationError({"contact_number": "Contact number is required."})
            # Barangay is optional when a landmark / GPS location is provided.
            if not attrs.get("barangay") and not landmark:
                raise serializers.ValidationError(
                    {"landmark": "Enter a landmark, or choose a barangay."}
                )
        attrs["reporter_name"] = reporter_name
        attrs["contact_number"] = contact
        attrs["landmark"] = landmark
        return attrs

    def validate_incident_ref_no(self, value):
        if not value:
            return value
        request = self.context["request"]
        if not request.user or not request.user.is_authenticated:
            raise serializers.ValidationError("Sign in to link an incident report.")
        report = IncidentReport.objects.filter(ref_no=value, reporter=request.user).exclude(
            status=IncidentReport.Status.DRAFT
        ).first()
        if not report:
            raise serializers.ValidationError("Linked incident report was not found.")
        return value


class EmergencyRequestSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    barangay_id = serializers.IntegerField(source="barangay.id", read_only=True, allow_null=True)
    barangay = serializers.CharField(source="barangay.name", read_only=True, allow_null=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    incident_ref_no = serializers.CharField(source="incident_report.ref_no", read_only=True, allow_null=True)
    image_url = serializers.SerializerMethodField()
    reporter_display_name = serializers.SerializerMethodField()

    class Meta:
        model = EmergencyRequest
        fields = (
            "ref_no",
            "category",
            "category_label",
            "barangay_id",
            "barangay",
            "landmark",
            "description",
            "reporter_name",
            "reporter_display_name",
            "contact_number",
            "incident_ref_no",
            "image_url",
            "status",
            "status_label",
            "review_note",
            "created_at",
            "reviewed_at",
        )

    def get_reporter_display_name(self, emergency):
        if emergency.reporter_name:
            return emergency.reporter_name
        if emergency.requester_id:
            return emergency.requester.full_name
        return "Guest reporter"

    def get_image_url(self, emergency):
        if not emergency.image:
            return None
        request = self.context.get("request")
        path = f"/emergency-requests/{emergency.ref_no}/image/"
        if not request:
            return path
        return request.build_absolute_uri(path)
