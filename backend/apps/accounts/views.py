from django.core import signing
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.tenants.models import Membership

from .models import User
from .serializers import LoginSerializer, RegisterSerializer, ResendVerificationSerializer
from .services import send_verification_email, verify_email_token


class _PublicAuthView(APIView):
    """Open endpoints: no auth (so a stale token in the header can't break them), rate limited."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class RegisterView(_PublicAuthView):
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(user)
        return Response(
            {"detail": "Account created. Check your email for the verification link."},
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(_PublicAuthView):
    """GET (email link click) or POST {"token": "..."} (future frontend)."""

    def _handle(self, request):
        token = request.query_params.get("token") or request.data.get("token")
        if not token:
            return Response({"detail": "Token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = verify_email_token(token)
        except signing.SignatureExpired:
            return Response({"detail": "This link has expired. Request a new one."}, status=400)
        except signing.BadSignature:
            return Response({"detail": "Invalid verification link."}, status=400)
        return Response({"detail": "Email verified. You can now log in.", "email": user.email})

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)


class ResendVerificationView(_PublicAuthView):
    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email, is_email_verified=False).first()
        if user:
            send_verification_email(user)
        # Same answer either way, so this endpoint can't be used to discover registered emails.
        return Response({"detail": "If that account exists and is unverified, a new link was sent."})


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = []
        for m in Membership.objects.filter(user=user).select_related("tenant").order_by("created_at"):
            domain = m.tenant.get_primary_domain()
            memberships.append(
                {
                    "organization": m.tenant.name,
                    "domain": domain.domain if domain else None,
                    "role": m.role,
                }
            )
        return Response(
            {
                "id": user.pk,
                "email": user.email,
                "full_name": user.full_name,
                "is_email_verified": user.is_email_verified,
                "memberships": memberships,
            }
        )
