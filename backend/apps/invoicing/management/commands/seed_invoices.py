from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django_tenants.utils import tenant_context

from apps.crm.models import Client
from apps.invoicing import services
from apps.invoicing.models import Invoice, TaxRate


class Command(BaseCommand):
    help = "Add sample invoices to a tenant that already has the Week 3 demo clients (safe to run twice)."

    def add_arguments(self, parser):
        parser.add_argument("--subdomain", required=True)

    def handle(self, *args, **opts):
        from apps.tenants.models import Domain

        domain = Domain.objects.filter(
            domain=f"{opts['subdomain']}.{settings.BASE_DOMAIN}"
        ).select_related("tenant").first()
        if not domain:
            raise CommandError(f"No tenant with subdomain {opts['subdomain']}")

        with tenant_context(domain.tenant):
            if Invoice.objects.exists():
                self.stdout.write("Invoices already present, nothing to do.")
                return
            clients = {c.name: c for c in Client.objects.filter(
                name__in=["Northwind Traders", "Bluebird Studio", "Kestrel Labs"])}
            if len(clients) < 3:
                raise CommandError("Demo clients not found. Run: seed_demo --subdomain " + opts["subdomain"])

            services.ensure_default_tax_rates()
            gst18 = TaxRate.objects.get(rate_bps=1800).pk
            gst5 = TaxRate.objects.get(rate_bps=500).pk

            # So the demo shows both tax layouts: Northwind is in the seller's state (CGST+SGST),
            # Bluebird is in another state (IGST).
            profile = services.get_profile()
            if not profile.state:
                profile.state = "Karnataka"
                profile.save(update_fields=["state"])
            bluebird = clients["Bluebird Studio"]
            if not bluebird.state:
                bluebird.state = "Maharashtra"
                bluebird.save(update_fields=["state", "updated_at"])
            northwind = clients["Northwind Traders"]
            kestrel = clients["Kestrel Labs"]
            today = timezone.localdate()

            def make(client, lines, **extra):
                return services.create_invoice(None, client, lines=[
                    {"description": d, "quantity": q, "unit_price_minor": p, "tax_name": n, "tax_rate_bps": b}
                    for d, q, p, n, b in lines
                ], **extra)

            web = ("Website design and build", 1, 12000000, "GST 18%", 1800)
            hosting = ("Hosting and maintenance (hours)", 10, 150000, "GST 18%", 1800)
            retainer = ("Monthly SEO retainer", 1, 4500000, "GST 18%", 1800)
            books = ("Training handbook printing", 50, 12000, "GST 5%", 500)

            # 1. overdue: sent 45 days ago, 30-day terms
            inv = make(northwind, [web, hosting])
            services.send_invoice(inv, None, issue_date=today - timedelta(days=45))
            # 2. paid in full
            inv = make(northwind, [retainer], notes="Thank you for your business.")
            inv = services.send_invoice(inv, None, issue_date=today - timedelta(days=20))
            services.record_payment(inv, None, inv.total_minor, paid_on=today - timedelta(days=5), method="upi")
            # 3. partly paid
            inv = make(bluebird, [web])
            inv = services.send_invoice(inv, None, issue_date=today - timedelta(days=10))
            services.record_payment(inv, None, 5000000, paid_on=today - timedelta(days=2), reference="NEFT-88231")
            # 4. sent, not yet due
            inv = make(kestrel, [retainer, books])
            services.send_invoice(inv, None, issue_date=today - timedelta(days=3))
            # 5. draft
            make(bluebird, [hosting], notes="Draft for next month.")

        self.stdout.write(self.style.SUCCESS(f"Demo invoices added to {opts['subdomain']}."))
