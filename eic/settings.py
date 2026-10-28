import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
# Prefer project-root .env; also load parent (legacy Desktop/.env layouts).
load_dotenv(BASE_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("SESSION_SECRET") or "eic-local-development-only"
DEBUG = os.getenv("DEBUG", "true").lower() in {"1", "true", "yes", "on"}
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "10.0.2.2",
    "10.144.19.198",
    ".replit.dev",
    ".replit.app",
    "*",
    *[host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()],
]
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "https://*.replit.dev",
    "https://*.replit.app",
    # Microsoft Dev Tunnels (browser Origin may be https tunnel or http://localhost:8000).
    "https://*.devtunnels.ms",
    "http://*.devtunnels.ms",
    "https://*.asse.devtunnels.ms",
    "http://*.asse.devtunnels.ms",
    "https://bb6cjv85-8000.asse.devtunnels.ms",
    "http://bb6cjv85-8000.asse.devtunnels.ms",
    *[origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()],
]
# Dev Tunnels terminate TLS; keep Host as what the client used when forwarded.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
# Dev Tunnel / mobile WebViews sometimes omit Referer; still accept POSTs in DEBUG.
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "accounts",
    "incidents",
    "classifier",
    "evaluation",
    "dashboard",
    "api.apps.ApiConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "eic.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "eic.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASES = {
    "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600) if DATABASE_URL else {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.UsernameOrMobileBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL = "/media/"
MEDIA_ROOT = ROOT_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

EIC_CATEGORIES = ["fire", "crime", "medical", "accident", "disaster"]
EIC_CATEGORY_LABELS = {
    "fire": "Fire",
    "crime": "Crime",
    "medical": "Medical",
    "accident": "Accident",
    "disaster": "Disaster",
}
EIC_LOW_CONFIDENCE_THRESHOLD = float(os.getenv("EIC_LOW_CONFIDENCE_THRESHOLD", "0.60"))
EIC_MAX_UPLOAD_MB = int(os.getenv("EIC_MAX_UPLOAD_MB", "10"))
EIC_ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"]
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
# Santa Maria, Ilocos Sur — default map center
EIC_MAP_CENTER_LAT = float(os.getenv("EIC_MAP_CENTER_LAT", "17.3665"))
EIC_MAP_CENTER_LNG = float(os.getenv("EIC_MAP_CENTER_LNG", "120.4818"))
EIC_MAP_DEFAULT_ZOOM = int(os.getenv("EIC_MAP_DEFAULT_ZOOM", "13"))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_RATES": {
        "eic_login": "10/min",
        "eic_classify": "20/hour",
    },
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Emergency Incident Classifier API",
    "DESCRIPTION": "Reporter API for the MDRRMO of Santa Maria, Ilocos Sur.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "eic.api": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "classifier": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "incidents.notifications": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_URLS_REGEX = r"^/api/.*$"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
