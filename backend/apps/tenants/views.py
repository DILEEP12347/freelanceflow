from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import SignupSerializer
from .services import create_tenant


class SignupView(APIView):
    def post(self, request):
        s = SignupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        tenant = create_tenant(**s.validated_data)
        domain = tenant.domains.first().domain
        return Response(
            {"name": tenant.name, "schema": tenant.schema_name, "domain": domain,
             "url": f"http://{domain}:8000/"},
            status=status.HTTP_201_CREATED,
        )
