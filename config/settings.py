import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
# Load .env for local development.
# On Render, environment variables are configured in the dashboard.
load_dotenv(override=False)   # CHANGED: was load_dotenv(). override=True makes your .env beat stray Windows variables.

BASE_DIR = Path(__file__).resolve().parent.parent

import logging

logger = logging.getLogger(__name__)

logger.warning("TURFIO BASE_DIR: %s", BASE_DIR)

logger.warning(
    "TURFIO TEMPLATE EXISTS: %s",
    (BASE_DIR / "templates" / "public" / "landing.html").is_file()
)

logger.warning(
    "TURFIO TEMPLATES FOLDER EXISTS: %s",
    (BASE_DIR / "templates").is_dir()
)

# Security settings
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

SECRET_KEY = os.environ.get("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is missing. Add it to your .env file locally "
        "or configure it in your hosting environment."
    )
# Trust HTTPS forwarded by Vercel
SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)

# Production HTTPS security
SECURE_SSL_REDIRECT = not DEBUG

# Secure cookies
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# HSTS: start cautiously
SECURE_HSTS_SECONDS = 0

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "turfio-two.vercel.app",
    ".vercel.app",
]

CSRF_TRUSTED_ORIGINS = [
    "https://turfio-two.vercel.app",
]

vercel_url = os.environ.get("VERCEL_URL")

if vercel_url:
    ALLOWED_HOSTS.append(vercel_url)
    CSRF_TRUSTED_ORIGINS.append(
        f"https://{vercel_url}"
    )

SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https"
)

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "crispy_forms",
    "crispy_bootstrap5",
    "core",
]

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# Middleware
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.TenantMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# URL and WSGI configuration
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"


# Templates
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
                "core.context.tenant_nav",
            ],
        },
    },
]


# Database: PostgreSQL
DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        conn_max_age=0,
        conn_health_checks=True,
        ssl_require=True,
    )
}

DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# User-uploaded media
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# Email configuration
# Console backend is suitable for local development.
# Configure SMTP environment variables before enabling email delivery.
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": os.environ.get("EMAIL_HOST", ""),
            "port": int(os.environ.get("EMAIL_PORT", "587")),
            "username": os.environ.get("EMAIL_HOST_USER", ""),
            "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
            "use_tls": True,
        },
    },
}

DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    "Turfio <noreply@turfio.local>",
)


# Authentication redirects
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

PASSWORD_RESET_TIMEOUT = 60 * 60 * 3  # 3 hours


# Default primary key field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
