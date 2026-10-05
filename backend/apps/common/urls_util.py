from django.conf import settings


def site_url(host: str) -> str:
    """http://acme.localhost:8000 (port omitted when SITE_PORT is empty, e.g. in production)."""
    port = f":{settings.SITE_PORT}" if settings.SITE_PORT else ""
    return f"{settings.SITE_SCHEME}://{host}{port}"
