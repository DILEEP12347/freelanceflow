import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.billing import gateway
from apps.billing.limits import FeatureNotAvailable, has_feature, requires_feature
from apps.billing.models import Plan, StripeEvent, Subscription
from apps.billing.plans import effective_plan_for, ensure_default_plans, get_subscription
from apps.tenants.models import Role
from apps.tenants.services import create_tenant

from .helpers import client_for, make_user
from .test_crm_clients import CrmBase

SECRET = "whsec_test"
PRICES = {"pro": "price_pro", "business": "price_biz"}
WEBHOOK = "/api/billing/webhook/"
CHECKOUT = "https://checkout.stripe.test/c/pay"


@override_settings(STRIPE_SECRET_KEY="sk_test_x", STRIPE_WEBHOOK_SECRET=SECRET, STRIPE_PRICE_IDS=PRICES)
class BillingBase(CrmBase):
    def setUp(self):
        super().setUp()
        ensure_default_plans()

    # ---- helpers
    def sub(self):
        return get_subscription(self.tenant)

    def set_plan(self, code, status="active", **fields):
        sub = self.sub()
        sub.plan = Plan.objects.get(code=code)
        sub.status = status
        for name, value in fields.items():
            setattr(sub, name, value)
        sub.save()
        return sub

    def set_limits(self, code, **limits):
        plan = Plan.objects.get(code=code)
        plan.limits = {**plan.limits, **limits}
        plan.save()

    def link_customer(self, customer="cus_1"):
        sub = self.sub()
        sub.stripe_customer_id = customer
        sub.save()

    def summary(self):
        return self.api.get("/api/billing/subscription/").data

    # ---- webhooks
    def public(self):
        return client_for(host="localhost")

    def deliver(self, event, secret=SECRET, ts=None, headers=True):
        body = json.dumps(event).encode()
        extra = {}
        if headers:
            stamp = int(time.time()) if ts is None else ts
            digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
            extra["HTTP_STRIPE_SIGNATURE"] = f"t={stamp},v1={digest}"
        return self.public().post(WEBHOOK, data=body, content_type="application/json", **extra)

    _n = 0

    def event(self, etype, obj, created=None, event_id=None):
        BillingBase._n += 1
        return {"id": event_id or f"evt_{BillingBase._n}", "type": etype,
                "created": created or int(time.time()), "data": {"object": obj}}

    def sub_obj(self, status="active", price="price_pro", customer="cus_1", **extra):
        return {"id": "sub_1", "customer": customer, "status": status, "cancel_at_period_end": False,
                "metadata": {"tenant_id": str(self.tenant.pk)},
                "current_period_end": int(time.time()) + 30 * 86400,
                "items": {"data": [{"price": {"id": price}}]}, **extra}


class PlanCatalogTests(BillingBase):
    def test_plans_endpoint_lists_three_plans_without_stripe_ids(self):
        r = self.api.get("/api/billing/plans/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p["code"] for p in r.data], ["free", "pro", "business"])
        self.assertNotIn("stripe_price_id", r.data[0])
        self.assertEqual(r.data[0]["limits"]["max_active_clients"], 5)
        self.assertIsNone(r.data[2]["limits"]["max_active_clients"])      # null = unlimited

    def test_everyone_can_read_billing_but_unauthenticated_cannot(self):
        self.assertEqual(self.as_role(Role.VIEWER).get("/api/billing/subscription/").status_code, 200)
        self.assertEqual(client_for(host=self.host).get("/api/billing/plans/").status_code, 401)

    def test_new_organization_starts_on_free(self):
        s = self.summary()
        self.assertEqual((s["plan"]["code"], s["effective_plan"]["code"], s["status"]), ("free", "free", "active"))
        self.assertTrue(s["can_start_trial"])
        self.assertFalse(s["has_billing_account"])
        self.assertEqual(s["usage"]["active_clients"], {"used": 0, "limit": 5})
        self.assertEqual(s["usage"]["team_members"]["used"], 1)            # the owner


class LimitTests(BillingBase):
    def test_free_plan_blocks_the_sixth_active_client(self):
        for i in range(5):
            self.make_client(f"Client {i}")
        r = self.api.post("/api/clients/", {"name": "One too many"}, format="json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.data["code"], "plan_limit_reached")
        self.assertEqual((r.data["limit"], r.data["used"], r.data["plan"]), (5, 5, "free"))
        self.assertEqual(self.summary()["usage"]["active_clients"], {"used": 5, "limit": 5})

    def test_archiving_frees_a_slot_and_restoring_is_limited_too(self):
        ids = [self.make_client(f"Client {i}")["id"] for i in range(5)]
        self.api.post(f"/api/clients/{ids[0]}/archive/")
        self.assertEqual(self.api.post("/api/clients/", {"name": "Fits now"}, format="json").status_code, 201)
        r = self.api.post(f"/api/clients/{ids[0]}/restore/")              # back to 6 active: over the limit
        self.assertEqual(r.status_code, 402)

    def test_paid_plan_lifts_the_limit_and_lapsing_brings_it_back_without_deleting_data(self):
        for i in range(5):
            self.make_client(f"Client {i}")
        self.set_plan("pro")
        self.assertEqual(self.api.post("/api/clients/", {"name": "Sixth"}, format="json").status_code, 201)
        self.set_plan("pro", status="canceled")
        self.assertEqual(self.api.post("/api/clients/", {"name": "Seventh"}, format="json").status_code, 402)
        self.assertEqual(self.api.get("/api/clients/").data["count"], 6)   # nothing was hidden or deleted

    def test_unlimited_plan(self):
        self.set_plan("business")
        for i in range(7):
            self.make_client(f"Client {i}")

    def test_converting_a_lead_creates_a_client_only_within_the_limit(self):
        ids = [self.make_client(f"Client {i}")["id"] for i in range(5)]
        lead = self.api.post("/api/leads/", {"title": "Big deal", "company_name": "NewCo"}, format="json").data
        self.assertEqual(self.api.post(f"/api/leads/{lead['id']}/convert/").status_code, 402)
        # linking to a client that already exists does not add an active client
        r = self.api.post(f"/api/leads/{lead['id']}/convert/", {"client_id": ids[0]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_invoice_limit_counts_sent_invoices_not_drafts(self):
        self.set_limits("free", max_invoices_per_month=2)
        cid = self.make_client("Invoiced Co")["id"]
        ids = []
        for _ in range(3):
            r = self.api.post("/api/invoices/", {"client": cid, "lines": [{"description": "Work", "unit_price_minor": 1000}]}, format="json")
            self.assertEqual(r.status_code, 201)                           # drafts are never limited
            ids.append(r.data["id"])
        self.assertEqual(self.api.post(f"/api/invoices/{ids[0]}/send/", {}, format="json").status_code, 200)
        self.assertEqual(self.api.post(f"/api/invoices/{ids[1]}/send/", {}, format="json").status_code, 200)
        r = self.api.post(f"/api/invoices/{ids[2]}/send/", {}, format="json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.data["limit_key"], "max_invoices_per_month")
        self.assertEqual(self.summary()["usage"]["invoices_this_month"], {"used": 2, "limit": 2})

    def test_team_limit_counts_members_and_pending_invites_and_reinviting_is_free(self):
        self.set_limits("free", max_team_members=2)                        # the owner already uses one seat
        invite = lambda email: self.api.post("/api/team/invites/", {"email": email, "role": "viewer"}, format="json")  # noqa: E731
        self.assertEqual(invite("one@example.com").status_code, 201)
        self.assertEqual(invite("one@example.com").status_code, 201)       # re-invite: replaces, same seat
        r = invite("two@example.com")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.data["limit_key"], "max_team_members")


class FeatureFlagTests(BillingBase):
    def test_features_follow_the_effective_plan(self):
        self.assertFalse(has_feature(self.tenant, "recurring_invoices"))
        self.set_plan("pro")
        self.assertTrue(has_feature(self.tenant, "recurring_invoices"))
        self.assertFalse(has_feature(self.tenant, "reports"))
        self.set_plan("pro", status="canceled")
        self.assertFalse(has_feature(self.tenant, "recurring_invoices"))

    def test_requires_feature_permission_answers_402(self):
        request = APIRequestFactory().get("/")
        request.tenant = self.tenant
        permission = requires_feature("recurring_invoices")()
        with self.assertRaises(FeatureNotAvailable) as ctx:
            permission.has_permission(request, None)
        self.assertEqual(ctx.exception.status_code, 402)
        self.assertEqual(ctx.exception.detail["feature"], "recurring_invoices")
        self.set_plan("pro")
        self.assertTrue(permission.has_permission(request, None))

    def test_no_tenant_means_nothing_is_limited(self):                    # management commands
        connection.set_schema_to_public()
        from apps.billing.limits import enforce_limit
        enforce_limit(None, "max_active_clients", used=10**6)


class CheckoutTests(BillingBase):
    def checkout(self, plan="pro", api=None):
        return (api or self.api).post("/api/billing/checkout/", {"plan": plan}, format="json")

    def test_owner_gets_a_checkout_url_and_a_stripe_customer_is_remembered(self):
        with patch.object(gateway, "create_customer", return_value="cus_new") as customer, \
             patch.object(gateway, "create_checkout_session", return_value={"id": "cs_1", "url": CHECKOUT}) as session:
            r = self.checkout("pro")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["url"], CHECKOUT)
        self.assertEqual(customer.call_args.kwargs["tenant_id"], self.tenant.pk)
        kwargs = session.call_args.kwargs
        self.assertEqual((kwargs["price_id"], kwargs["plan_code"], kwargs["trial_days"]), ("price_pro", "pro", 14))
        self.assertEqual(kwargs["customer_id"], "cus_new")
        self.assertIn("{CHECKOUT_SESSION_ID}", kwargs["success_url"])
        self.assertEqual(self.sub().stripe_customer_id, "cus_new")
        self.assertEqual(self.sub().plan.code, "free")                     # only the webhook changes the plan

    def test_second_checkout_reuses_the_customer_and_gives_no_second_trial(self):
        self.link_customer("cus_existing")
        sub = self.sub()
        sub.trial_used = True
        sub.save()
        with patch.object(gateway, "create_customer") as customer, \
             patch.object(gateway, "create_checkout_session", return_value={"id": "cs_2", "url": CHECKOUT}) as session:
            self.assertEqual(self.checkout("business").status_code, 200)
        customer.assert_not_called()
        self.assertEqual(session.call_args.kwargs["trial_days"], 0)
        self.assertEqual(session.call_args.kwargs["customer_id"], "cus_existing")

    def test_only_the_owner_can_start_checkout(self):
        for role in (Role.ADMIN, Role.ACCOUNTANT, Role.VIEWER):
            self.assertEqual(self.checkout(api=self.as_role(role)).status_code, 403, role)

    def test_bad_plans_are_rejected(self):
        self.assertEqual(self.checkout("platinum").status_code, 400)
        self.assertEqual(self.checkout("free").status_code, 400)
        self.assertEqual(self.api.post("/api/billing/checkout/", {}, format="json").status_code, 400)

    def test_cannot_check_out_twice(self):
        self.link_customer()
        self.set_plan("pro", stripe_subscription_id="sub_1")
        self.assertEqual(self.checkout("business").status_code, 400)

    @override_settings(STRIPE_SECRET_KEY="")
    def test_unconfigured_server_answers_503_not_500(self):
        self.assertEqual(self.checkout("pro").status_code, 503)

    @override_settings(STRIPE_PRICE_IDS={})
    def test_plan_without_a_price_answers_503(self):
        self.assertEqual(self.checkout("pro").status_code, 503)

    def test_portal(self):
        self.assertEqual(self.api.post("/api/billing/portal/", {}, format="json").status_code, 400)   # no customer yet
        self.link_customer("cus_9")
        with patch.object(gateway, "create_portal_session", return_value="https://billing.stripe.test/p") as portal:
            r = self.api.post("/api/billing/portal/", {}, format="json")
        self.assertEqual((r.status_code, r.data["url"]), (200, "https://billing.stripe.test/p"))
        self.assertEqual(portal.call_args.args[0], "cus_9")
        self.assertEqual(self.as_role(Role.ADMIN).post("/api/billing/portal/", {}, format="json").status_code, 403)

    def test_change_plan_asks_stripe_to_prorate_and_waits_for_the_webhook(self):
        self.link_customer()
        self.set_plan("pro", stripe_subscription_id="sub_1")
        with patch.object(gateway, "change_subscription_price") as change:
            r = self.api.post("/api/billing/change-plan/", {"plan": "business"}, format="json")
        self.assertEqual(r.status_code, 202, r.data)
        change.assert_called_once_with("sub_1", "price_biz", "business")
        self.assertEqual(self.sub().plan.code, "pro")                      # not applied until Stripe confirms

    def test_change_plan_rules(self):
        post = lambda plan: self.api.post("/api/billing/change-plan/", {"plan": plan}, format="json")  # noqa: E731
        self.assertEqual(post("business").status_code, 400)               # no subscription yet
        self.set_plan("pro", stripe_subscription_id="sub_1")
        self.assertEqual(post("pro").status_code, 400)                    # already on it
        self.assertEqual(post("free").status_code, 400)                   # downgrade to free = cancel in the portal


class WebhookSecurityTests(BillingBase):
    def test_signature_is_required_and_checked(self):
        event = self.event("customer.subscription.updated", self.sub_obj())
        self.assertEqual(self.deliver(event, headers=False).status_code, 400)
        self.assertEqual(self.deliver(event, secret="whsec_wrong").status_code, 400)
        self.assertEqual(self.deliver(event, ts=int(time.time()) - 3600).status_code, 400)   # replay of an old request
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_garbage_body_is_rejected(self):
        body = b"not json"
        digest = hmac.new(SECRET.encode(), f"{int(time.time())}.".encode() + body, hashlib.sha256).hexdigest()
        r = self.public().post(WEBHOOK, data=body, content_type="application/json",
                               HTTP_STRIPE_SIGNATURE=f"t={int(time.time())},v1={digest}")
        self.assertEqual(r.status_code, 400)

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_unconfigured_secret_answers_503(self):
        self.assertEqual(self.deliver(self.event("invoice.paid", {})).status_code, 503)

    def test_webhook_is_not_exposed_on_tenant_domains(self):
        r = client_for(self.owner, self.host).post(WEBHOOK, data=b"{}", content_type="application/json")
        self.assertEqual(r.status_code, 404)

    def test_unhandled_event_types_are_acknowledged_and_not_stored(self):
        r = self.deliver(self.event("charge.succeeded", {"id": "ch_1"}))
        self.assertEqual((r.status_code, r.data["status"]), (200, "ignored"))
        self.assertEqual(StripeEvent.objects.count(), 0)


class WebhookStateTests(BillingBase):
    def setUp(self):
        super().setUp()
        self.link_customer("cus_1")

    def test_checkout_completed_links_the_subscription_id(self):
        r = self.deliver(self.event("checkout.session.completed", {
            "id": "cs_1", "mode": "subscription", "customer": "cus_1", "subscription": "sub_77",
            "client_reference_id": str(self.tenant.pk)}))
        self.assertEqual(r.data["status"], "processed")
        self.assertEqual(self.sub().stripe_subscription_id, "sub_77")

    def test_subscription_created_in_trial_activates_the_plan(self):
        trial_end = int(time.time()) + 14 * 86400
        r = self.deliver(self.event("customer.subscription.created",
                                    self.sub_obj(status="trialing", trial_end=trial_end)))
        self.assertEqual(r.status_code, 200, r.data)
        sub = self.sub()
        self.assertEqual((sub.plan.code, sub.status, sub.trial_used), ("pro", "trialing", True))
        self.assertEqual(int(sub.trial_end.timestamp()), trial_end)
        s = self.summary()
        self.assertEqual((s["effective_plan"]["code"], s["can_start_trial"]), ("pro", False))

    def test_both_stripe_api_shapes_for_the_period_end(self):
        end = int(time.time()) + 20 * 86400
        new_shape = self.sub_obj(price="price_biz")
        del new_shape["current_period_end"]
        new_shape["items"]["data"][0]["current_period_end"] = end
        self.deliver(self.event("customer.subscription.updated", new_shape))
        sub = self.sub()
        self.assertEqual((sub.plan.code, int(sub.current_period_end.timestamp())), ("business", end))

    def test_upgrade_cancel_scheduling_and_unknown_price(self):
        self.deliver(self.event("customer.subscription.created", self.sub_obj(price="price_pro")))
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(price="price_biz", cancel_at_period_end=True)))
        sub = self.sub()
        self.assertEqual((sub.plan.code, sub.cancel_at_period_end), ("business", True))
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(price="price_mystery")))
        self.assertEqual(self.sub().plan.code, "business")                 # an unknown price never changes the plan

    def test_duplicate_delivery_is_applied_once(self):
        event = self.event("customer.subscription.created", self.sub_obj())
        self.assertEqual(self.deliver(event).data["status"], "processed")
        self.set_plan("free")                                              # change state, then replay the same event
        self.assertEqual(self.deliver(event).data["status"], "duplicate")
        self.assertEqual(self.sub().plan.code, "free")                     # the replay did nothing
        self.assertEqual(StripeEvent.objects.filter(event_id=event["id"]).count(), 1)

    def test_failed_processing_rolls_back_so_stripe_can_retry(self):
        event = self.event("customer.subscription.created", self.sub_obj())
        with patch.dict("apps.billing.webhooks.HANDLERS", {"customer.subscription.created": self._boom}):
            with self.assertRaises(RuntimeError):
                self.deliver(event)
        self.assertEqual(StripeEvent.objects.count(), 0)                   # the id was NOT kept
        self.assertEqual(self.deliver(event).data["status"], "processed")  # the retry succeeds
        self.assertEqual(self.sub().plan.code, "pro")

    @staticmethod
    def _boom(event, obj):
        raise RuntimeError("database hiccup")

    def test_late_old_events_are_ignored(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(price="price_biz"), created=now))
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(price="price_pro"), created=now - 500))
        self.assertEqual(self.sub().plan.code, "business")

    def test_payment_failure_starts_a_grace_period_then_access_drops_to_free(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(), created=now - 100))
        self.deliver(self.event("invoice.payment_failed", {"id": "in_1", "customer": "cus_1"}, created=now - 50))
        sub = self.sub()
        self.assertEqual(sub.status, "past_due")
        self.assertAlmostEqual(sub.grace_until.timestamp(), now - 50 + 7 * 86400, delta=5)
        self.assertEqual(self.summary()["effective_plan"]["code"], "pro")  # still inside the grace period

        sub.grace_until = timezone.now() - timedelta(minutes=1)            # time passes
        sub.save()
        s = self.summary()
        self.assertEqual((s["plan"]["code"], s["effective_plan"]["code"], s["entitled"]), ("pro", "free", False))
        for i in range(5):
            self.make_client(f"Client {i}")
        self.assertEqual(self.api.post("/api/clients/", {"name": "Sixth"}, format="json").status_code, 402)  # Free limits apply again

    def test_payment_success_clears_the_grace_period(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(), created=now - 100))
        self.deliver(self.event("invoice.payment_failed", {"id": "in_1", "customer": "cus_1"}, created=now - 50))
        self.deliver(self.event("invoice.paid", {"id": "in_2", "customer": "cus_1"}, created=now - 10))
        sub = self.sub()
        self.assertEqual((sub.status, sub.grace_until), ("active", None))
        self.assertIsNotNone(sub.last_payment_at)

    def test_a_payment_failure_for_a_free_organization_changes_nothing(self):
        self.deliver(self.event("invoice.payment_failed", {"id": "in_x", "customer": "cus_1"}))
        sub = self.sub()
        self.assertEqual((sub.plan.code, sub.status, sub.grace_until), ("free", "active", None))

    def test_subscription_deleted_returns_to_free_and_allows_a_new_checkout(self):
        self.deliver(self.event("customer.subscription.created", self.sub_obj()))
        self.deliver(self.event("customer.subscription.deleted", self.sub_obj(status="canceled")))
        sub = self.sub()
        self.assertEqual((sub.plan.code, sub.status, sub.stripe_subscription_id), ("free", "canceled", ""))
        self.assertTrue(effective_plan_for(sub).is_free)
        with patch.object(gateway, "create_checkout_session", return_value={"id": "cs", "url": CHECKOUT}) as session:
            r = self.api.post("/api/billing/checkout/", {"plan": "pro"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(session.call_args.kwargs["trial_days"], 0)        # the trial was already used

    def test_events_only_touch_the_matching_organization(self):
        connection.set_schema_to_public()
        other_owner = make_user("owner@other.test")
        other = create_tenant("Other", "other", owner=other_owner)
        other_sub = get_subscription(other)
        other_sub.stripe_customer_id = "cus_other"
        other_sub.save()
        self.deliver(self.event("customer.subscription.created", self.sub_obj(customer="cus_other", metadata={"tenant_id": str(other.pk)})))
        self.assertEqual(Subscription.objects.get(pk=other_sub.pk).plan.code, "pro")
        self.assertEqual(self.sub().plan.code, "free")

    def test_full_lifecycle_through_the_read_api(self):
        now = int(time.time())
        self.assertEqual(self.summary()["effective_plan"]["code"], "free")
        self.deliver(self.event("checkout.session.completed", {"mode": "subscription", "customer": "cus_1", "subscription": "sub_1"}, created=now - 400))
        self.deliver(self.event("customer.subscription.created", self.sub_obj(status="trialing", trial_end=now + 86400), created=now - 300))
        self.assertEqual(self.summary()["status"], "trialing")
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(status="active"), created=now - 200))
        self.assertEqual((self.summary()["status"], self.summary()["effective_plan"]["code"]), ("active", "pro"))
        self.deliver(self.event("invoice.payment_failed", {"customer": "cus_1"}, created=now - 100))
        self.assertEqual(self.summary()["status"], "past_due")
        self.deliver(self.event("customer.subscription.deleted", self.sub_obj(status="canceled"), created=now - 50))
        s = self.summary()
        self.assertEqual((s["status"], s["effective_plan"]["code"], s["has_billing_account"]), ("canceled", "free", True))
