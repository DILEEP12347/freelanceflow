from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.tenants.models import Domain, Membership, Role


class Command(BaseCommand):
    help = (
        "Give an existing user a role in an existing tenant (e.g. tenants created in Week 1 that "
        "have no members yet). Also marks the user's email verified: the operator vouches for them."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--subdomain", required=True)
        parser.add_argument("--role", default="owner", choices=Role.values)

    def handle(self, *args, **opts):
        user = User.objects.filter(email__iexact=opts["email"]).first()
        if not user:
            raise CommandError(f"No user with email {opts['email']}")
        domain = Domain.objects.filter(domain=f"{opts['subdomain']}.{settings.BASE_DOMAIN}").first()
        if not domain:
            raise CommandError(f"No tenant with subdomain {opts['subdomain']}")
        membership, _ = Membership.objects.update_or_create(
            user=user, tenant=domain.tenant, defaults={"role": opts["role"]}
        )
        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])
        self.stdout.write(self.style.SUCCESS(f"{user.email} is now {membership.role} of {domain.tenant.name}"))
