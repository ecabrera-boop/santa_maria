from rest_framework.permissions import BasePermission


class IsMobileAccount(BasePermission):
    message = "Only reporter and administrator accounts can use the mobile API."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.role in {"reporter", "admin"}
        )


class IsAdminRole(BasePermission):
    message = "Administrator access is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.role == "admin")
