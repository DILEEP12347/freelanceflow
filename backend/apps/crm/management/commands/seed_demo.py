from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.crm.models import ActivityKind, Client, Contact, Lead, LeadStage, Note, Tag
from apps.crm.services import log_activity
from apps.tenants.models import Domain


class Command(BaseCommand):
    help = "Fill a tenant with sample tags, clients, contacts, notes and leads (safe to run twice)."

    def add_arguments(self, parser):
        parser.add_argument("--subdomain", required=True)

    def handle(self, *args, **opts):
        domain = Domain.objects.filter(
            domain=f"{opts['subdomain']}.{settings.BASE_DOMAIN}"
        ).select_related("tenant").first()
        if not domain:
            raise CommandError(f"No tenant with subdomain {opts['subdomain']}")

        with schema_context(domain.tenant.schema_name):
            if Client.objects.filter(name="Northwind Traders").exists():
                self.stdout.write("Demo data already present, nothing to do.")
                return

            vip, _ = Tag.objects.get_or_create(name="VIP", defaults={"color": "#b45309"})
            retainer, _ = Tag.objects.get_or_create(name="Retainer", defaults={"color": "#1d4ed8"})
            startup, _ = Tag.objects.get_or_create(name="Startup", defaults={"color": "#15803d"})

            def make_client(name, tags, **fields):
                client = Client.objects.create(name=name, **fields)
                client.tags.set(tags)
                log_activity(ActivityKind.CLIENT_CREATED, f"Created {name}", client=client)
                return client

            northwind = make_client(
                "Northwind Traders", [vip, retainer], company_name="Northwind Traders Pvt Ltd",
                email="accounts@northwind.example", phone="+91 80 5550 0101", city="Bengaluru",
                state="Karnataka", country="India", tax_id="29AAAAA0000A1Z5", payment_terms_days=30,
            )
            make_client("Bluebird Studio", [startup], email="hello@bluebird.example", city="Mumbai", country="India")
            make_client("Kestrel Labs", [retainer], email="pay@kestrel.example", city="Hyderabad", country="India")

            Contact.objects.create(client=northwind, name="Meera Nair", email="meera@northwind.example",
                                   job_title="Finance Manager", is_primary=True)
            log_activity(ActivityKind.CONTACT_ADDED, "Added contact Meera Nair", client=northwind)
            note = Note.objects.create(client=northwind, body="Prefers invoices by the 1st of the month.",
                                       author_email="demo@freelanceflow.local")
            log_activity(ActivityKind.NOTE_ADDED, note.body, client=northwind, metadata={"note_id": note.pk})

            samples = [
                ("Brand refresh", "Anil Rao", "Orbit Foods", LeadStage.LEAD, 8000000),
                ("Mobile app MVP", "Sara Khan", "Pixelwave", LeadStage.CONTACTED, 25000000),
                ("Website rebuild", "Dev Patel", "Harbor & Co", LeadStage.PROPOSAL, 12000000),
                ("Logo package", "Isha Verma", "Freshly", LeadStage.WON, 3000000),
                ("SEO audit", "Tom Becker", "Lumen", LeadStage.LOST, 1500000),
            ]
            for title, person, company, stage, value in samples:
                lead = Lead.objects.create(
                    title=title, contact_name=person, company_name=company, stage=stage, value_minor=value,
                    contact_email=f"{person.split()[0].lower()}@{company.split()[0].lower()}.example",
                    closed_at=timezone.now() if stage in (LeadStage.WON, LeadStage.LOST) else None,
                )
                log_activity(ActivityKind.LEAD_CREATED, f"Lead created: {title}", lead=lead,
                             metadata={"stage": stage})

        self.stdout.write(self.style.SUCCESS(f"Demo data added to {opts['subdomain']}."))
