from django import forms
from django.contrib.auth import authenticate

from accounts.models import User, mobile_number_validator, username_validator
from incidents.models import Barangay


class StaffLoginForm(forms.Form):
    username = forms.CharField(max_length=150, label="Username")
    password = forms.CharField(widget=forms.PasswordInput, label="Password")

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        username = cleaned.get("username")
        password = cleaned.get("password")
        if username and password:
            self.user_cache = authenticate(self.request, username=username.strip(), password=password)
            if not self.user_cache or not self.user_cache.is_active:
                raise forms.ValidationError("Username or password is incorrect.")
            if self.user_cache.role not in {User.Role.STAFF, User.Role.ADMIN}:
                raise forms.ValidationError("This account does not have staff access.")
        return cleaned

    def get_user(self):
        return self.user_cache


class DashboardUserForm(forms.ModelForm):
    last_name = forms.CharField(max_length=80, label="Last name")
    first_name = forms.CharField(max_length=80, label="First name")
    middle_name = forms.CharField(max_length=80, required=False, label="Middle name")
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the current password when editing.",
        min_length=8,
    )
    password_confirm = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Confirm password",
    )

    class Meta:
        model = User
        fields = (
            "last_name",
            "first_name",
            "middle_name",
            "username",
            "mobile_number",
            "role",
            "barangay",
            "is_active",
        )
        widgets = {
            "username": forms.TextInput(attrs={"autocomplete": "username"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Dela Cruz", "autocomplete": "family-name"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Juan", "autocomplete": "given-name"}),
            "middle_name": forms.TextInput(attrs={"placeholder": "Santos", "autocomplete": "additional-name"}),
            "mobile_number": forms.TextInput(attrs={"placeholder": "09XXXXXXXXX"}),
        }

    def __init__(self, *args, **kwargs):
        self.is_create = kwargs.pop("is_create", False)
        super().__init__(*args, **kwargs)
        self.fields["barangay"].queryset = Barangay.objects.filter(is_active=True).order_by("name")
        self.fields["barangay"].required = False
        self.fields["username"].validators = [username_validator]
        self.fields["mobile_number"].validators = [mobile_number_validator]
        for name, field in self.fields.items():
            css = "select-control" if name in {"role", "barangay"} else "text-control"
            if name == "is_active":
                continue
            field.widget.attrs.setdefault("class", css)
        if self.is_create:
            self.fields["password"].required = True
            self.fields["password_confirm"].required = True
            self.fields["password"].help_text = "Minimum 8 characters."

    def clean_last_name(self):
        return User.normalize_name_part(self.cleaned_data["last_name"])

    def clean_first_name(self):
        return User.normalize_name_part(self.cleaned_data["first_name"])

    def clean_middle_name(self):
        return User.normalize_name_part(self.cleaned_data.get("middle_name", ""))

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("That username is already taken.")
        return username

    def clean_mobile_number(self):
        mobile = self.cleaned_data["mobile_number"].strip()
        qs = User.objects.filter(mobile_number=mobile)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("That mobile number is already registered.")
        return mobile

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("last_name"):
            self.add_error("last_name", "Enter the last name.")
        if not cleaned.get("first_name"):
            self.add_error("first_name", "Enter the first name.")
        password = cleaned.get("password") or ""
        confirm = cleaned.get("password_confirm") or ""
        if self.is_create and not password:
            self.add_error("password", "Enter a password for the new account.")
        if password or confirm:
            if password != confirm:
                self.add_error("password_confirm", "The two password fields did not match.")
            elif len(password) < 8:
                self.add_error("password", "Password must be at least 8 characters.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.last_name = self.cleaned_data["last_name"]
        user.first_name = self.cleaned_data["first_name"]
        user.middle_name = self.cleaned_data.get("middle_name", "")
        user.sync_full_name()
        password = self.cleaned_data.get("password") or ""
        if password:
            user.set_password(password)
        user.is_staff = user.role in {User.Role.STAFF, User.Role.ADMIN}
        user.is_superuser = user.role == User.Role.ADMIN
        if commit:
            user.save()
        return user
