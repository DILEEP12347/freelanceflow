import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.billing.limits import enforce_limit
from apps.common.urls_util import site_url

from .models import Domain, Invitation, Membership, Role, Tenant

SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$")
RESERVED = {"www", "api", "admin", "app", "static", "mail", "public", "billing", "support"}
INVITE_TTL = timedelta(days=7)


def validate_subdomain(subdomain: str) -> str:
    subdomain = subdomain.strip().lower()
    if not SUBDOMAIN_RE.match(subdomain):
        raise ValidationError(
            {"subdomain": "3-30 chars: lowercase letters, digits, hyphens; cannot start/end with a hyphen."}
        )
    if subdomain in RESERVED:
        raise ValidationError({"subdomain": "This subdomain is reserved."})
    if Domain.objects.filter(domain=f"{subdomain}.{settings.BASE_DOMAIN}").exists():
        raise ValidationError({"subdomain": "This subdomain is already taken."})
    return subdomain


def create_tenant(name: str, subdomain: str, owner) -> Tenant:
    """Create tenant + Postgres schema + domain, and make `owner` its Owner.
    Later weeks: move schema provisioning into a Celery task with a 'provisioning' status."""
    subdomain = validate_subdomain(subdomain)
    schema_name = "t_" + subdomain.replace("-", "_")  # schema names can't contain hyphens
    with transaction.atomic():
        tenant = Tenant(name=name, schema_name=schema_name)
        tenant.save()  # triggers schema creation
        Domain.objects.create(
            domain=f"{subdomain}.{settings.BASE_DOMAIN}", tenant=tenant, is_primary=True
        )
        Membership.objects.create(user=owner, tenant=tenant, role=Role.OWNER)
    return tenant


# ---------------- invitations ----------------
def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def seats_in_use(tenant: Tenant, exclude_email=None) -> int:
    """Members plus still-valid pending invitations: what the plan's team-size limit counts."""
    pending = Invitation.objects.filter(tenant=tenant, accepted_at__isnull=True, expires_at__gt=timezone.now())
    if exclude_email:
        pending = pending.exclude(email__iexact=exclude_email)
    return Membership.objects.filter(tenant=tenant).count() + pending.count()


def create_invitation(tenant: Tenant, email: str, role: str, invited_by):
    """Returns (invitation, raw_token). The raw token exists only in the email; we keep its hash."""
    email = email.strip().lower()
    # Members plus pending invites count as seats. Re-inviting the same address does not take a second seat.
    enforce_limit(tenant, "max_team_members", seats_in_use(tenant, exclude_email=email))
    # Re-inviting the same address replaces the old pending invite.
    Invitation.objects.filter(tenant=tenant, email__iexact=email, accepted_at__isnull=True).delete()
    raw = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        tenant=tenant,
        email=email,
        role=role,
        token_hash=hash_token(raw),
        invited_by=invited_by,
        expires_at=timezone.now() + INVITE_TTL,
    )
    return invitation, raw


def send_invitation_email(invitation: Invitation, raw_token: str) -> None:
    domain = invitation.tenant.get_primary_domain()
    base = site_url(domain.domain if domain else settings.BASE_DOMAIN)
    inviter = invitation.invited_by.full_name or invitation.invited_by.email if invitation.invited_by else "A teammate"
    send_mail(
        subject=f"You're invited to {invitation.tenant.name} on FreelanceFlow",
        message=(
            f"{inviter} invited you to join {invitation.tenant.name} as {invitation.role}.\n\n"
            f"1. Register or log in with THIS email address ({invitation.email}).\n"
            f"2. POST the token below to {base}/api/team/invites/accept/\n\n"
            f"Token: {raw_token}\n\n"
            f"The invitation expires in 7 days.\n"
        ),
        from_email=None,
        recipient_list=[invitation.email],
    )


def accept_invitation(raw_token, user, tenant) -> Membership:
    invalid = ValidationError({"token": "This invitation is invalid, expired, or already used."})
    if not raw_token or not isinstance(raw_token, str):
        raise invalid
    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update()
            .filter(token_hash=hash_token(raw_token), tenant=tenant, accepted_at__isnull=True)
            .first()
        )
        if invitation is None or invitation.is_expired:
            raise invalid
        if invitation.email.lower() != user.email.lower():
            raise ValidationError({"token": "This invitation was sent to a different email address."})
        if not user.is_email_verified:
            raise ValidationError({"token": "Verify your email address first."})
        membership, _ = Membership.objects.get_or_create(
            user=user, tenant=tenant, defaults={"role": invitation.role}
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
    return membership
