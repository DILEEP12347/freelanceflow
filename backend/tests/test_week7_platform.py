import time
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from apps.billing.models import StripeEvent
from apps.billing.plans import get_subscription
from apps.tenants.models import Tenant
from apps.tenants.services import create_tenant

from .helpers import client_for, make_user
from .test_week7_work import Week7Base

PUBLIC = "localhost"


class PlatformBase(Week7Base):
    def root(self):
        user = make_user("root@platform.test")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        return client_for(user, PUBLIC)

    def new_org(self, name, subdomain, plan=None, status="active", **fields):
        connection.set_schema_to_public()
        tenant = create_tenant(name, subdomain, owner=make_user(f"owner@{subdomain}.test"))
        if plan:
            sub = get_subscription(tenant)
            sub.plan = type(sub.plan).objects.get(code=plan)
            sub.status = status
            for key, value in fields.items():
                setattr(sub, key, value)
            sub.save()
        return tenant


class PlatformAccessTests(PlatformBase):
    URLS = ["/api/platform/overview/", "/api/platform/organizations/", "/api/platform/billing-events/"]

    def test_only_superusers_get_in(self):
        admin = self.root()                                                                      # one user, reused
        for url in self.URLS:
            self.assertEqual(client_for(host=PUBLIC).get(url).status_code, 401, url)
            self.assertEqual(client_for(self.owner, PUBLIC).get(url).status_code, 403, url)      # an org owner is not a platform admin
            self.assertEqual(admin.get(url).status_code, 200, url)

    def test_it_does_not_exist_on_an_organizations_own_domain(self):
        for url in self.URLS:
            self.assertEqual(client_for(self.owner, self.host).get(url).status_code, 404, url)


class OverviewTests(PlatformBase):
    def setUp(self):
        super().setUp()
        self.set_plan("pro")                                                                    # acme: paying
        self.new_org("Free Co", "freeco")                                                       # no subscription = Free
        self.new_org("Trial Co", "trialco", "business", "trialing")
        self.new_org("Late Co", "lateco", "pro", "past_due", grace_until=timezone.now() - timedelta(hours=1))

    def test_counts_mrr_and_plan_mix(self):
        data = self.root().get("/api/platform/overview/").data
        self.assertEqual(data["organizations"], 4)                                              # the public tenant is not counted
        mix = {p["code"]: p["organizations"] for p in data["plans"]}
        self.assertEqual(mix, {"free": 2, "pro": 1, "business": 1})                             # Late Co lapsed to Free
        self.assertEqual(data["subscriptions"], {"paying": 1, "trialing": 1, "past_due": 1})
        self.assertEqual(data["mrr"], [{"currency": "INR", "amount_minor": 79900}])             # only acme is really paying
        self.assertEqual(data["arr"], [{"currency": "INR", "amount_minor": 79900 * 12}])
        self.assertEqual(sum(g["organizations"] for g in data["growth"]), 4)
        self.assertEqual(data["new_organizations_30d"], 4)

    def test_a_past_due_customer_inside_the_grace_period_still_counts_as_paying(self):
        self.new_org("Grace Co", "graceco", "pro", "past_due", grace_until=timezone.now() + timedelta(days=3))
        data = self.root().get("/api/platform/overview/").data
        self.assertEqual(data["subscriptions"]["paying"], 2)
        self.assertEqual(data["mrr"], [{"currency": "INR", "amount_minor": 79900 * 2}])

    def test_churn_from_stripe_events(self):
        now = int(time.time())
        for i, (kind, age_days) in enumerate([("deleted", 1), ("deleted", 5), ("deleted", 60), ("created", 2)]):
            StripeEvent.objects.create(event_id=f"evt_{i}", type=f"customer.subscription.{kind}", created_ts=now - age_days * 86400)
        churn = self.root().get("/api/platform/overview/").data["churn_30d"]
        self.assertEqual((churn["cancellations"], churn["new_subscriptions"]), (2, 1))          # the 60-day-old one is outside the window
        self.assertEqual(churn["rate"], round(2 / (1 + 2), 4))                                  # 2 lost vs 1 paying now + 2 lost


class OrganizationListTests(PlatformBase):
    def setUp(self):
        super().setUp()
        self.set_plan("pro")
        self.new_org("Free Co", "freeco")
        self.new_org("Trial Co", "trialco", "business", "trialing")
        self.new_org("Late Co", "lateco", "pro", "past_due", grace_until=timezone.now() - timedelta(hours=1))
        self.admin = self.root()

    def rows(self, **params):
        r = self.admin.get("/api/platform/organizations/", params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["results"]

    def test_rows(self):
        data = self.admin.get("/api/platform/organizations/").data
        self.assertEqual(data["count"], 4)
        self.assertNotIn("public", [r["schema_name"] for r in data["results"]])
        acme = next(r for r in data["results"] if r["domain"] == "acme.localhost")
        self.assertEqual((acme["owner_email"], acme["members"], acme["plan"], acme["effective_plan"], acme["status"]),
                         (self.owner.email, 1, "pro", "pro", "active"))
        late = next(r for r in data["results"] if r["domain"] == "lateco.localhost")
        self.assertEqual((late["plan"], late["effective_plan"], late["status"]), ("pro", "free", "past_due"))
        free = next(r for r in data["results"] if r["domain"] == "freeco.localhost")
        self.assertEqual((free["plan"], free["status"], free["has_stripe_customer"]), ("free", "active", False))

    def test_filters(self):
        domains = lambda **p: sorted(r["domain"] for r in self.rows(**p))  # noqa: E731
        self.assertEqual(domains(q="Trial"), ["trialco.localhost"])
        self.assertEqual(domains(q="lateco"), ["lateco.localhost"])                             # by domain
        self.assertEqual(domains(q=f"owner@freeco"), ["freeco.localhost"])                      # by owner email
        self.assertEqual(domains(plan="business"), ["trialco.localhost"])
        self.assertEqual(domains(plan="free"), ["freeco.localhost"])
        self.assertEqual(domains(status="trialing"), ["trialco.localhost"])
        self.assertEqual(domains(status="past_due"), ["lateco.localhost"])
        self.assertEqual(domains(status="active"), ["acme.localhost", "freeco.localhost"])

    def test_ordering_and_paging(self):
        names = [r["name"] for r in self.rows(ordering="name")]
        self.assertEqual(names, sorted(names))
        self.assertEqual([r["name"] for r in self.rows(ordering="-name")], sorted(names, reverse=True))
        self.assertEqual(self.admin.get("/api/platform/organizations/", {"ordering": "password"}).status_code, 400)
        self.assertEqual(self.admin.get("/api/platform/organizations/", {"page": 1}).data["count"], 4)


class OrganizationDetailTests(PlatformBase):
    def test_detail_with_team_usage_and_limits(self):
        self.set_plan("pro")
        self.make_client("One")
        self.make_client("Two")
        inv = self.make_invoice()
        self.send(inv["id"])
        tenant = Tenant.objects.get(schema_name=self.tenant.schema_name)
        data = self.root().get(f"/api/platform/organizations/{tenant.pk}/").data
        self.assertEqual(data["team"], [{"email": self.owner.email, "role": "owner"}])
        self.assertEqual((data["usage"]["active_clients"], data["usage"]["invoices_sent"]), (3, 1))   # the invoice made a client too
        self.assertIsNotNone(data["usage"]["last_activity_at"])
        self.assertEqual(data["plan_limits"]["max_active_clients"], 50)
        self.assertEqual(data["recent_billing_events"], [])

    def test_unknown_ids_and_the_public_tenant_are_404(self):
        admin = self.root()
        self.assertEqual(admin.get("/api/platform/organizations/999999/").status_code, 404)
        public = Tenant.objects.get(schema_name="public")
        self.assertEqual(admin.get(f"/api/platform/organizations/{public.pk}/").status_code, 404)


class BillingEventsTests(PlatformBase):
    def test_recent_events(self):
        StripeEvent.objects.create(event_id="evt_a", type="invoice.paid", customer_id="cus_1", created_ts=1)
        StripeEvent.objects.create(event_id="evt_b", type="customer.subscription.deleted", customer_id="cus_2", created_ts=2)
        rows = self.root().get("/api/platform/billing-events/").data
        self.assertEqual({r["event_id"] for r in rows}, {"evt_a", "evt_b"})
