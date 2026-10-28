from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import User, UserManagementLog


class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("username", "mobile_number", "last_name", "first_name", "middle_name", "role")

    def clean_password2(self):
        first = self.cleaned_data.get("password1")
        second = self.cleaned_data.get("password2")
        if first and second and first != second:
            raise forms.ValidationError("The two password fields did not match.")
        return second

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        user.sync_full_name()
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField()

    class Meta:
        model = User
        fields = "__all__"

    def clean_password(self):
        return self.initial.get("password")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    ordering = ("username",)
    list_display = ("username", "mobile_number", "full_name", "last_name", "first_name", "role", "barangay", "is_active")
    list_filter = ("role", "is_active", "barangay")
    search_fields = ("username", "mobile_number", "full_name", "last_name", "first_name", "middle_name")
    fieldsets = (
        (None, {"fields": ("username", "mobile_number", "password")}),
        ("Profile", {"fields": ("last_name", "first_name", "middle_name", "full_name", "role", "barangay", "privacy_consent_at", "expo_push_token")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "mobile_number",
                    "last_name",
                    "first_name",
                    "middle_name",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    filter_horizontal = ("groups", "user_permissions")
    readonly_fields = ("last_login", "date_joined")


@admin.register(UserManagementLog)
class UserManagementLogAdmin(admin.ModelAdmin):
    list_display = ("target_user", "action", "performed_by", "summary", "created_at")
    list_filter = ("action",)
    search_fields = ("target_user__username", "target_user__full_name", "summary")
    readonly_fields = ("created_at",)
