from django.db import connection
from django.http import JsonResponse


def health(request):
    """Shows which tenant/schema served the request. Great for debugging routing."""
    tenant = request.tenant
    return JsonResponse(
        {"status": "ok", "tenant": tenant.name, "schema": connection.schema_name}
    )
