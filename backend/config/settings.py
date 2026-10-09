import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
# ".localhost" matches localhost AND acme.localhost, beta.localhost, ...
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", ".localhost,127.0.0.1").split(",")
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "localhost")

# Used to build links inside emails. In production: SITE_SCHEME=https, SITE_PORT=""
SITE_SCHEME = os.environ.get("SITE_SCHEME", "http")
SITE_PORT = os.environ.get("SITE_PORT", "8000")

# --- django-tenants: which apps live where -------------------------------
SHARED_APPS = [
    "django_tenants",  # must be first
    "apps.tenants",
    "apps.billing",  # Week 5: plans, subscriptions, Stripe events (public schema)
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
    "apps.crm",
    "apps.business",  # Week 2: BusinessProfile (one per tenant schema)
    "apps.invoicing",  # Week 4: invoices, line items, tax rates, payments
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- DRF + JWT -------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    # Secure by default: every view must explicitly opt OUT of auth (AllowAny).
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {"auth": "60/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
}

# --- Email: prints to the `web` container logs in development -------------
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "FreelanceFlow <no-reply@freelanceflow.local>")
# Real email: set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend plus the four values below in .env.
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
# Run tasks inline (no Redis, no worker) while testing, or when CELERY_TASK_ALWAYS_EAGER=1 is set in .env.
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "") == "1" or "test" in sys.argv
CELERY_TASK_EAGER_PROPAGATES = True
BUSINESS_TZ_CACHE_SECONDS = 0 if "test" in sys.argv else 30  # how long an organization's timezone is remembered
CELERY_BEAT_SCHEDULE = {
    # Hourly, because "9am" is different in every organization's timezone. Each organization's daily jobs
    # (recurring invoices, payment reminders) run once per local day, at the first run after 9:00 their time.
    "business-daily-jobs": {"task": "invoicing.run_scheduled_jobs", "schedule": 3600.0},
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
WSGI_APPLICATION = "config.wsgi.application"


# --- Billing (Stripe, Week 5) ---
# Use TEST keys (sk_test_...) while developing. Never commit real keys: they belong in .env.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")  # whsec_... from `stripe listen` or the dashboard
STRIPE_PRICE_IDS = {  # Stripe Price ids (price_...) for the paid plans; a Plan row's stripe_price_id also works
    "pro": os.environ.get("STRIPE_PRICE_PRO", ""),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", ""),
}
BILLING_GRACE_DAYS = int(os.environ.get("BILLING_GRACE_DAYS", "7"))  # paid features stay on this long after a failed payment
BILLING_RETURN_PATH = os.environ.get("BILLING_RETURN_PATH", "/settings/billing")  # where Stripe sends people back to
