from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import SignupSerializer
from .services import create_tenant


class SignupView(APIView):
    """Create an organization. Requires login; the caller becomes its Owner."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_email_verified:
            raise PermissionDenied("Verify your email before creating an organization.")
        s = SignupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        tenant = create_tenant(owner=request.user, **s.validated_data)
        domain = tenant.get_primary_domain().domain
        return Response(
            {
                "name": tenant.name,
                "schema": tenant.schema_name,
                "domain": domain,
                "url": f"http://{domain}:8000/",
                "role": "owner",
            },
            status=status.HTTP_201_CREATED,
        )
