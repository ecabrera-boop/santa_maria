import re

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models

from incidents.models import Barangay


mobile_number_validator = RegexValidator(
    regex=r"^09\d{9}$",
    message="Enter an 11-digit Philippine mobile number starting with 09.",
)

username_validator = RegexValidator(
    regex=r"^[\w.@+-]+$",
    message="Enter a valid username. Letters, digits, and @/./+/-/_ only.",
)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, mobile_number, password, **extra_fields):
        if not mobile_number:
            raise ValueError("A mobile number is required.")
        mobile_number = re.sub(r"\s+", "", mobile_number)
        if not username:
            username = mobile_number
        username = str(username).strip()
        user = self.model(username=username, mobile_number=mobile_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username=None, mobile_number=None, password=None, **extra_fields):
        # Support legacy call style: create_user(mobile_number="09...", ...)
        if mobile_number is None and username and re.fullmatch(r"09\d{9}", str(username)):
            mobile_number = username
            username = None
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, mobile_number, password, **extra_fields)

    def create_superuser(self, username=None, mobile_number=None, password=None, **extra_fields):
        if mobile_number is None and username and re.fullmatch(r"09\d{9}", str(username)):
            mobile_number = username
            username = None
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have staff and superuser permissions.")
        return self._create_user(username, mobile_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        REPORTER = "reporter", "Reporter"
        STAFF = "staff", "MDRRMO staff"
        ADMIN = "admin", "Administrator"

    username = models.CharField(max_length=150, unique=True, validators=[username_validator])
    mobile_number = models.CharField(max_length=11, unique=True, validators=[mobile_number_validator])
    last_name = models.CharField(max_length=80, blank=True)
    first_name = models.CharField(max_length=80, blank=True)
    middle_name = models.CharField(max_length=80, blank=True)
    full_name = models.CharField(max_length=160)
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.REPORTER)
    barangay = models.ForeignKey(Barangay, null=True, blank=True, on_delete=models.SET_NULL, related_name="users")
    privacy_consent_at = models.DateTimeField(null=True, blank=True)
    expo_push_token = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name", "mobile_number"]

    def __str__(self):
        return f"{self.full_name} ({self.username})"

    @staticmethod
    def normalize_name_part(value):
        cleaned = re.sub(r"\s+", " ", (value or "").strip())
        if not cleaned:
            return ""
        return " ".join(
            (word[:1].upper() + word[1:].lower()) if word else ""
            for word in cleaned.split(" ")
        )

    @classmethod
    def compose_full_name(cls, last_name, first_name, middle_name=""):
        last_name = cls.normalize_name_part(last_name)
        first_name = cls.normalize_name_part(first_name)
        middle_name = cls.normalize_name_part(middle_name)
        given = " ".join(part for part in (first_name, middle_name) if part).strip()
        if last_name and given:
            return f"{last_name}, {given}"
        return last_name or given

    def sync_full_name(self):
        composed = self.compose_full_name(self.last_name, self.first_name, self.middle_name)
        if composed:
            self.full_name = composed
        return self.full_name

    def save(self, *args, **kwargs):
        self.last_name = self.normalize_name_part(self.last_name)
        self.first_name = self.normalize_name_part(self.first_name)
        self.middle_name = self.normalize_name_part(self.middle_name)
        if self.last_name or self.first_name:
            self.sync_full_name()
        elif self.full_name:
            self.full_name = self.normalize_name_part(self.full_name)
        super().save(*args, **kwargs)

    @property
    def is_dashboard_user(self):
        return self.role in {self.Role.STAFF, self.Role.ADMIN} and self.is_active

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN and self.is_active


class UserManagementLog(models.Model):
    """Audit trail for dashboard user-management actions."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ACTIVATED = "activated", "Activated"
        DEACTIVATED = "deactivated", "Deactivated"
        PASSWORD_RESET = "password_reset", "Password reset"

    target_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="management_logs")
    performed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="performed_user_management_actions",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    summary = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.get_action_display()} · {self.target_user}"
