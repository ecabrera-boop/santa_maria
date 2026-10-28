import secrets

from django.core.management.base import BaseCommand

from accounts.models import User


class Command(BaseCommand):
    help = "Create one administrator, one MDRRMO staff user, and one reporter for local demos."

    def handle(self, *args, **options):
        accounts = [
            ("admin", "09170000001", "EIC Demo Administrator", User.Role.ADMIN, True, True),
            ("staff", "09170000002", "EIC Demo Staff", User.Role.STAFF, False, False),
            ("reporter", "09170000003", "EIC Demo Reporter", User.Role.REPORTER, False, False),
        ]
        for username, mobile, name, role, is_staff, is_superuser in accounts:
            user = User.objects.filter(username=username).first() or User.objects.filter(mobile_number=mobile).first()
            if user:
                if user.username != username:
                    user.username = username
                    user.save(update_fields=["username"])
                self.stdout.write(
                    self.style.WARNING(f"{role.title()} already exists: {user.username} (password unchanged)")
                )
                continue
            password = secrets.token_urlsafe(14)
            user = User.objects.create_user(
                username=username,
                mobile_number=mobile,
                password=password,
                full_name=name,
                role=role,
                is_staff=is_staff,
                is_superuser=is_superuser,
            )
            self.stdout.write(
                self.style.SUCCESS(f"{role.title()}: username {user.username} / password {password}")
            )
