import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
# ".localhost" matches localhost AND acme.localhost, beta.localhost, ...
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", ".localhost,127.0.0.1").split(",")
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "localhost")

# --- django-tenants: which apps live where -------------------------------
SHARED_APPS = [
    "django_tenants",  # must be first
    "apps.tenants",
    "apps.accounts",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin",
    "django.contrib.staticfiles",
    "rest_framework",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "apps.crm",  # Week 3 expands this: clients, invoices, ...
]

INSTALLED_APPS = list(SHARED_APPS) + [a for a in TENANT_APPS if a not in SHARED_APPS]

TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"
PUBLIC_SCHEMA_URLCONF = "config.public_urls"
ROOT_URLCONF = "config.urls"

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",  # must be first
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ.get("POSTGRES_DB", "freelanceflow"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],  # tightened in Week 2
}

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
WSGI_APPLICATION = "config.wsgi.application"
