from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class UsernameOrMobileBackend(ModelBackend):
    """Authenticate with either username (staff) or mobile number (reporters)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        identifier = str(username).strip()
        user = (
            UserModel._default_manager.filter(username__iexact=identifier).first()
            or UserModel._default_manager.filter(mobile_number=identifier).first()
        )
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        # Mitigate timing attacks when the user does not exist.
        UserModel().set_password(password)
        return None
