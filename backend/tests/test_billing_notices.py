import time
from unittest.mock import patch

from django.core import mail

from apps.billing import webhooks

from .test_billing_api import BillingBase


class BillingNoticeTests(BillingBase):
    def setUp(self):
        super().setUp()
        self.link_customer("cus_1")

    def failed(self, created, invoice="in_1"):
        return self.event("invoice.payment_failed", {"id": invoice, "customer": "cus_1"}, created=created)

    def test_a_failed_payment_emails_the_owner_once_not_once_per_retry(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(), created=now - 100))
        self.assertEqual(len(mail.outbox), 0)
        self.deliver(self.failed(now - 50))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.owner.email])
        self.assertIn("did not go through", message.subject)
        self.assertIn("Pro", message.subject)
        self.deliver(self.failed(now - 40, invoice="in_2"))                     # Stripe retried the card and it failed again
        self.assertEqual(len(mail.outbox), 1)

    def test_subscription_update_then_invoice_failure_is_still_one_email(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.updated", self.sub_obj(status="past_due"), created=now - 50))
        self.deliver(self.failed(now - 40))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_recovered_payment_that_fails_again_later_emails_again(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(), created=now - 300))
        self.deliver(self.failed(now - 200))
        self.deliver(self.event("invoice.paid", {"id": "in_ok", "customer": "cus_1"}, created=now - 100))
        self.deliver(self.failed(now - 50, invoice="in_3"))
        self.assertEqual(len(mail.outbox), 2)

    def test_trial_ending_email(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(status="trialing", trial_end=now + 3 * 86400), created=now - 100))
        r = self.deliver(self.event("customer.subscription.trial_will_end", self.sub_obj(status="trialing"), created=now - 50))
        self.assertEqual(r.data["status"], "processed")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("trial", mail.outbox[0].subject)

    def test_no_trial_email_for_a_subscription_that_is_not_trialing(self):
        now = int(time.time())
        self.deliver(self.event("customer.subscription.created", self.sub_obj(status="active"), created=now - 100))
        self.deliver(self.event("customer.subscription.trial_will_end", self.sub_obj(status="active"), created=now - 50))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_free_organization_gets_no_payment_email(self):
        self.deliver(self.failed(int(time.time())))
        self.assertEqual(len(mail.outbox), 0)

    def test_an_event_that_rolls_back_sends_nothing(self):
        def explode(event, obj):
            webhooks._notify(webhooks._find_subscription(obj), "payment_failed")
            raise RuntimeError("database hiccup")

        with patch.dict("apps.billing.webhooks.HANDLERS", {"invoice.payment_failed": explode}):
            with self.assertRaises(RuntimeError):
                self.deliver(self.failed(int(time.time())))
        self.assertEqual(len(mail.outbox), 0)
