from django.conf import settings
from django.core import signing
from django.core.mail import send_mail

from apps.common.urls_util import site_url

VERIFY_SALT = "freelanceflow.verify-email"
VERIFY_MAX_AGE = 60 * 60 * 48  # 48 hours


def make_verification_token(user) -> str:
    # Signed + timestamped, nothing stored in the DB. Tamper-proof thanks to SECRET_KEY.
    return signing.dumps({"uid": user.pk}, salt=VERIFY_SALT)


def verify_email_token(token: str):
    """Marks the user verified. Raises signing.SignatureExpired / signing.BadSignature."""
    from .models import User

    data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
    user = User.objects.filter(pk=data.get("uid")).first()
    if user is None:
        raise signing.BadSignature("Unknown user")
    if not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])
    return user


def send_verification_email(user) -> None:
    link = f"{site_url(settings.BASE_DOMAIN)}/api/auth/verify-email/?token={make_verification_token(user)}"
    send_mail(
        subject="Verify your FreelanceFlow email",
        message=(
            f"Hi {user.full_name or user.email},\n\n"
            f"Confirm your email by opening this link (valid for 48 hours):\n\n{link}\n"
        ),
        from_email=None,
        recipient_list=[user.email],
    )
