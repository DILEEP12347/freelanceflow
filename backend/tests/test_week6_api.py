from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone as dt_utc
from io import StringIO
from urllib.parse import urlparse
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from django_tenants.utils import tenant_context

from apps.invoicing import emails, services, tasks
from apps.invoicing.models import Invoice, InvoiceReminder, InvoiceSettings, RecurringInvoice
from apps.invoicing.recurrence import add_months
from apps.invoicing.timeutils import business_today
from apps.tenants.models import Role

from .helpers import client_for
from .test_billing_api import BillingBase
from .test_invoicing_api import InvoiceBase


@contextmanager
def at_day(day, hour=12):
    """Pretend it is `day` (UTC noon). Everything that asks Django for the time sees it."""
    fake = datetime.combine(day, time(hour), tzinfo=dt_utc.utc)
    with patch("django.utils.timezone.now", return_value=fake):
        yield


class Week6Base(InvoiceBase, BillingBase):
    def setUp(self):
        super().setUp()
        cache.clear()  # the portal rate limiter remembers requests between tests
        self.today = timezone.localdate()

    def in_tenant(self):
        return tenant_context(self.tenant)

    def emailed_client(self, name="Mail Co", email="billing@mailco.test", **extra):
        return self.make_client(name, email=email, **extra)["id"]

    def sent_invoice(self, due_in=10, client_id=None, **extra):
        cid = client_id or self.emailed_client(f"Co {timezone.now().timestamp()}")
        inv = self.make_invoice(cid, **extra)
        due = self.today + timedelta(days=due_in)
        r = self.send(inv["id"], issue_date=self.today.isoformat(), due_date=due.isoformat())
        self.assertEqual(r.status_code, 200, r.data)
        return r.data, due

    def token(self, invoice_id):
        with self.in_tenant():
            return Invoice.objects.get(pk=invoice_id).portal_token


class SettingsTests(Week6Base):
    URL = "/api/invoicing/settings/"

    def test_defaults_and_update(self):
        data = self.api.get(self.URL).data
        self.assertEqual((data["reminders_enabled"], data["reminder_offsets"], data["timezone"]), (True, [-3, 1, 7, 14], "UTC"))
        r = self.api.patch(self.URL, {"timezone": "Asia/Kolkata", "reminder_offsets": [14, 1, 7], "email_signature": "Thanks, Acme"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["timezone"], r.data["reminder_offsets"]), ("Asia/Kolkata", [1, 7, 14]))
        self.assertEqual(self.api.get(self.URL).data["email_signature"], "Thanks, Acme")

    def test_validation(self):
        bad = [{"timezone": "Mars/Base"}, {"reminder_offsets": [True]}, {"reminder_offsets": [200]}, {"reminder_offsets": [1, 1]},
               {"reminder_offsets": [1, 2, 3, 4, 5, 6, 7]}, {"reminder_offsets": "7"}]
        for body in bad:
            self.assertEqual(self.api.patch(self.URL, body, format="json").status_code, 400, body)

    def test_roles(self):
        viewer = self.as_role(Role.VIEWER)
        self.assertEqual(viewer.get(self.URL).status_code, 200)
        self.assertEqual(viewer.patch(self.URL, {"reminders_enabled": False}, format="json").status_code, 403)
        self.assertEqual(self.as_role(Role.ACCOUNTANT).patch(self.URL, {"reminders_enabled": False}, format="json").status_code, 200)

    def test_the_organizations_timezone_decides_what_today_is(self):
        inv = self.make_invoice()
        with self.in_tenant():
            Invoice.objects.filter(pk=inv["id"]).update(status="sent", due_date=date(2026, 10, 7))
        instant = datetime(2026, 10, 7, 20, 0, tzinfo=dt_utc.utc)  # 01:30 on the 8th in India
        with self.in_tenant(), patch("django.utils.timezone.now", return_value=instant):
            self.assertEqual(business_today(), date(2026, 10, 7))
            self.assertEqual(Invoice.objects.get(pk=inv["id"]).display_status, "sent")      # due today: not overdue yet
            cfg = InvoiceSettings.load()
            cfg.timezone = "Asia/Kolkata"
            cfg.save()
            self.assertEqual(business_today(), date(2026, 10, 8))
            self.assertEqual(Invoice.objects.get(pk=inv["id"]).display_status, "overdue")


class EmailTests(Week6Base):
    def test_sending_with_the_email_flag_emails_the_pdf(self):
        cid = self.emailed_client()
        inv = self.make_invoice(cid)
        r = self.send(inv["id"], email=True)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["email_queued"])
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["billing@mailco.test"])
        self.assertIn("INV-0001", message.subject)
        name, content, mimetype = message.attachments[0]
        self.assertEqual((name, mimetype), ("INV-0001.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertIsNotNone(self.api.get(f"/api/invoices/{inv['id']}/").data["emailed_at"])
        self.assertIn("invoice_emailed", self.kinds(f"/api/clients/{cid}/timeline/"))

    def test_plain_send_does_not_email(self):
        inv = self.make_invoice(self.emailed_client())
        self.assertFalse(self.send(inv["id"]).data["email_queued"])
        self.assertEqual(len(mail.outbox), 0)

    def test_no_recipient_is_reported_not_an_error(self):
        inv = self.make_invoice(self.make_client("No Mail Co")["id"])
        r = self.send(inv["id"], email=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["email_queued"])
        self.assertIn("No email address", r.data["email_note"])
        self.assertEqual(len(mail.outbox), 0)

    def test_email_action_rules(self):
        inv = self.make_invoice(self.emailed_client())
        url = f"/api/invoices/{inv['id']}/email/"
        self.assertEqual(self.api.post(url, {}, format="json").status_code, 400)           # still a draft
        self.send(inv["id"])
        self.assertEqual(self.as_role(Role.VIEWER).post(url, {}, format="json").status_code, 403)
        r = self.api.post(url, {"message": "Hello there, thanks!"}, format="json")
        self.assertEqual((r.status_code, r.data["to"]), (202, "billing@mailco.test"))
        self.assertIn("Hello there, thanks!", mail.outbox[0].body)
        self.pay(inv["id"], inv["total_minor"])
        self.assertEqual(self.api.post(url, {}, format="json").status_code, 400)           # nothing left to pay

    def test_email_action_without_any_address(self):
        inv = self.make_invoice(self.make_client("No Mail Co")["id"])
        self.send(inv["id"])
        self.assertEqual(self.api.post(f"/api/invoices/{inv['id']}/email/", {}, format="json").status_code, 400)

    def test_the_primary_contact_is_preferred_over_the_client_email(self):
        cid = self.emailed_client(email="general@co.test")
        self.api.post(f"/api/clients/{cid}/contacts/", {"name": "Pri", "email": "pri@co.test", "is_primary": True}, format="json")
        inv = self.make_invoice(cid)
        self.send(inv["id"])
        r = self.api.post(f"/api/invoices/{inv['id']}/email/", {}, format="json")
        self.assertEqual(r.data["to"], "pri@co.test")

    def test_the_portal_link_is_included_only_when_the_plan_has_the_portal(self):
        cid = self.emailed_client()
        first = self.make_invoice(cid)
        self.send(first["id"], email=True)
        self.assertNotIn("/portal/", mail.outbox[0].body)                                    # Free plan
        self.set_plan("pro")
        second = self.make_invoice(cid)
        self.send(second["id"], email=True)
        self.assertIn("/portal/", mail.outbox[1].body)

    def test_a_queued_email_for_an_invoice_that_got_paid_is_dropped(self):
        inv, _ = self.sent_invoice()
        self.pay(inv["id"], inv["total_minor"])
        with self.in_tenant():
            self.assertEqual(emails.deliver_invoice_email(inv["id"]), "skipped")
        self.assertEqual(len(mail.outbox), 0)

    def test_the_celery_task_works_on_its_own(self):
        inv, _ = self.sent_invoice()
        self.assertEqual(tasks.send_invoice_email(self.tenant.schema_name, inv["id"]), "sent")
        self.assertEqual(len(mail.outbox), 1)


class PortalTests(Week6Base):
    def link(self, invoice_id, method="get"):
        return getattr(self.api, method)(f"/api/invoices/{invoice_id}/portal-link/")

    def setUp(self):
        super().setUp()
        self.anon = client_for(host=self.host)

    def test_free_plan_has_no_portal(self):
        inv, _ = self.sent_invoice()
        r = self.link(inv["id"])
        self.assertEqual((r.data["enabled"], r.data["url"]), (False, None))
        self.assertEqual(self.anon.get(f"/portal/{self.token(inv['id'])}/").status_code, 404)

    def test_page_json_and_pdf_for_a_pro_organization(self):
        self.set_plan("pro")
        inv, _ = self.sent_invoice()
        url = self.link(inv["id"]).data["url"]
        self.assertIn("/portal/", url)
        token = url.rstrip("/").split("/")[-1]

        page = self.anon.get(f"/portal/{token}/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("INV-0001", html)
        self.assertIn("Download PDF", html)
        self.assertEqual(page["Cache-Control"], "no-store")
        self.assertIn("noindex", page["X-Robots-Tag"])

        data = self.anon.get(f"/api/portal/{token}/").data
        self.assertEqual(data["number"], "INV-0001")
        self.assertNotIn("email", data["to"])                  # staff-only details stay out
        self.assertNotIn("snapshot", data)
        self.assertTrue(data["pdf_url"].startswith("/portal/"))

        pdf = self.anon.get(f"/portal/{token}/pdf/")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_the_first_view_is_recorded_once(self):
        self.set_plan("pro")
        cid = self.emailed_client()
        inv, _ = self.sent_invoice(client_id=cid)
        token = self.token(inv["id"])
        self.assertIsNone(self.api.get(f"/api/invoices/{inv['id']}/").data["viewed_at"])
        self.anon.get(f"/portal/{token}/")
        self.anon.get(f"/portal/{token}/")
        self.assertIsNotNone(self.api.get(f"/api/invoices/{inv['id']}/").data["viewed_at"])
        self.assertEqual(self.kinds(f"/api/clients/{cid}/timeline/").count("portal_viewed"), 1)

    def test_unknown_short_and_draft_tokens_are_all_404(self):
        self.set_plan("pro")
        self.assertEqual(self.anon.get("/portal/short/").status_code, 404)
        self.assertEqual(self.anon.get("/portal/" + "x" * 32 + "/").status_code, 404)
        draft = self.make_invoice()
        self.assertEqual(self.link(draft["id"]).status_code, 400)           # drafts have no link
        with self.in_tenant():
            Invoice.objects.filter(pk=draft["id"]).update(portal_token="d" * 32)
        self.assertEqual(self.anon.get("/portal/" + "d" * 32 + "/").status_code, 404)

    def test_rotating_the_link_kills_the_old_one(self):
        self.set_plan("pro")
        inv, _ = self.sent_invoice()
        old = self.token(inv["id"])
        self.assertEqual(self.anon.get(f"/portal/{old}/").status_code, 200)
        new_url = self.link(inv["id"], "post").data["url"]
        self.assertNotIn(old, new_url)
        self.assertEqual(self.anon.get(f"/portal/{old}/").status_code, 404)
        self.assertEqual(self.anon.get(urlparse(new_url).path).status_code, 200)

    def test_the_link_stops_working_when_the_paid_plan_lapses(self):
        self.set_plan("pro")
        inv, _ = self.sent_invoice()
        token = self.token(inv["id"])
        self.assertEqual(self.anon.get(f"/portal/{token}/").status_code, 200)
        self.set_plan("pro", status="canceled")
        self.assertEqual(self.anon.get(f"/portal/{token}/").status_code, 404)

    def test_client_supplied_text_is_escaped(self):
        self.set_plan("pro")
        cid = self.emailed_client("<script>alert(1)</script>")
        inv, _ = self.sent_invoice(client_id=cid)
        html = self.anon.get(f"/portal/{self.token(inv['id'])}/").content.decode()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)


class ReminderTests(Week6Base):
    def reminder_rows(self, invoice_id):
        with self.in_tenant():
            return {r.offset_days: r.skipped for r in InvoiceReminder.objects.filter(invoice_id=invoice_id)}

    def run_on(self, day):
        with self.in_tenant(), at_day(day):
            return services.run_reminders(day)

    def test_reminders_fire_once_each_and_the_stale_one_is_skipped(self):
        cid = self.emailed_client()
        inv, due = self.sent_invoice(client_id=cid)
        self.assertEqual(self.run_on(due + timedelta(days=1)), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("overdue by 1 day", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].attachments[0][0], "INV-0001.pdf")
        self.assertEqual(self.run_on(due + timedelta(days=1)), 0)               # same day again: nothing
        self.assertEqual(self.run_on(due + timedelta(days=2)), 0)
        self.assertEqual(self.run_on(due + timedelta(days=7)), 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.reminder_rows(inv["id"]), {-3: True, 1: False, 7: False})                   # -3 was too old when 1 fired
        self.assertIn("reminder_sent", self.kinds(f"/api/clients/{cid}/timeline/"))

    def test_a_long_overdue_invoice_gets_one_email_not_a_burst(self):
        inv, due = self.sent_invoice()
        self.assertEqual(self.run_on(due + timedelta(days=20)), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("overdue by 20 days", mail.outbox[0].subject)
        self.assertEqual(self.reminder_rows(inv["id"]), {-3: True, 1: True, 7: True, 14: False})
        self.assertEqual(self.run_on(due + timedelta(days=21)), 0)

    def test_a_friendly_reminder_before_the_due_date(self):
        _, due = self.sent_invoice()
        self.assertEqual(self.run_on(due - timedelta(days=3)), 1)
        self.assertIn("is due in 3 days", mail.outbox[0].subject)

    def test_never_on_the_day_the_invoice_was_sent(self):
        inv = self.make_invoice(self.emailed_client())
        self.send(inv["id"], issue_date=(self.today - timedelta(days=5)).isoformat(), due_date=(self.today - timedelta(days=1)).isoformat())
        with self.in_tenant():
            self.assertEqual(services.run_reminders(business_today()), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_switch_and_custom_offsets(self):
        inv, due = self.sent_invoice()
        with self.in_tenant():
            cfg = InvoiceSettings.load()
            cfg.reminders_enabled = False
            cfg.save()
        self.assertEqual(self.run_on(due + timedelta(days=1)), 0)
        with self.in_tenant():
            cfg = InvoiceSettings.load()
            cfg.reminders_enabled, cfg.reminder_offsets = True, [2]
            cfg.save()
        self.assertEqual(self.run_on(due + timedelta(days=1)), 0)
        self.assertEqual(self.run_on(due + timedelta(days=2)), 1)

    def test_paid_and_void_invoices_are_left_alone(self):
        paid, due = self.sent_invoice()
        self.pay(paid["id"], paid["total_minor"])
        voided, _ = self.sent_invoice()
        self.api.post(f"/api/invoices/{voided['id']}/void/", {}, format="json")
        self.assertEqual(self.run_on(due + timedelta(days=1)), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_address_means_the_reminder_is_recorded_but_not_sent(self):
        inv = self.make_invoice(self.make_client("No Mail Co")["id"])
        due = self.today + timedelta(days=10)
        self.send(inv["id"], issue_date=self.today.isoformat(), due_date=due.isoformat())
        self.assertEqual(self.run_on(due + timedelta(days=1)), 0)
        with self.in_tenant():
            row = InvoiceReminder.objects.get(invoice_id=inv["id"], offset_days=1)
        self.assertEqual((row.skipped, row.to_email), (True, ""))
        self.assertEqual(len(mail.outbox), 0)


class RecurringBase(Week6Base):
    def setUp(self):
        super().setUp()
        self.set_plan("pro")

    def rec_payload(self, client_id, **extra):
        return {"name": "Monthly retainer", "client": client_id, "frequency": "monthly",
                "start_date": self.today.isoformat(), "lines": [self.line()], **extra}

    def make_rec(self, client_id=None, **extra):
        cid = client_id or self.emailed_client("Retainer Co", "retainer@co.test")
        r = self.api.post("/api/recurring-invoices/", self.rec_payload(cid, **extra), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def generate(self, day=None):
        with self.in_tenant():
            return services.generate_recurring(day or self.today)

    def rec(self, rec_id):
        with self.in_tenant():
            return RecurringInvoice.objects.get(pk=rec_id)

    def generated(self, rec_id):
        with self.in_tenant():
            return list(Invoice.objects.filter(recurring_id=rec_id).order_by("id"))


class RecurringFeatureTests(Week6Base):
    def test_the_free_plan_gets_a_402(self):
        for r in (self.api.get("/api/recurring-invoices/"),
                  self.api.post("/api/recurring-invoices/", {}, format="json")):
            self.assertEqual(r.status_code, 402)
            self.assertEqual(r.data["code"], "feature_not_available")


class RecurringCrudTests(RecurringBase):
    def test_create_and_validation(self):
        data = self.make_rec()
        self.assertEqual((data["status"], data["runs_count"], data["next_run_date"]), ("active", 0, self.today.isoformat()))
        self.assertEqual(data["estimated_total_minor"], 1180000)
        self.assertEqual(data["lines"][0]["tax_name"], "GST 18%")
        cid = self.emailed_client("Another Co", "a@co.test")
        bad = [{"start_date": (self.today - timedelta(days=1)).isoformat()}, {"lines": []},
               {"end_date": (self.today - timedelta(days=1)).isoformat()}, {"interval": 0}, {"frequency": "daily"},
               {"currency": "USD"}, {"max_runs": 0}]
        for extra in bad:
            r = self.api.post("/api/recurring-invoices/", self.rec_payload(cid, **extra), format="json")
            self.assertEqual(r.status_code, 400, extra)

    def test_roles_and_delete_keep_the_invoices(self):
        rec = self.make_rec()
        self.generate()
        viewer = self.as_role(Role.VIEWER)
        self.assertEqual(viewer.get("/api/recurring-invoices/").status_code, 200)
        self.assertEqual(viewer.post("/api/recurring-invoices/", {}, format="json").status_code, 403)
        url = f"/api/recurring-invoices/{rec['id']}/"
        self.assertEqual(self.as_role(Role.ACCOUNTANT).delete(url).status_code, 403)
        self.assertEqual(self.api.delete(url).status_code, 204)
        invoices = self.api.get("/api/invoices/").data["results"]
        self.assertEqual(len(invoices), 1)                                       # the created invoice survived

    def test_a_client_with_a_schedule_cannot_be_deleted(self):
        rec = self.make_rec()
        self.assertEqual(self.api.delete(f"/api/clients/{rec['client']}/").status_code, 409)

    def test_editing_rules(self):
        start = self.today + timedelta(days=5)
        rec = self.make_rec(start_date=start.isoformat())
        url = f"/api/recurring-invoices/{rec['id']}/"
        new_start = self.today + timedelta(days=9)
        r = self.api.patch(url, {"start_date": new_start.isoformat(), "name": "Renamed"}, format="json")
        self.assertEqual((r.status_code, r.data["next_run_date"], r.data["name"]), (200, new_start.isoformat(), "Renamed"))
        r = self.api.patch(url, {"lines": [self.line(price=500000, taxed=False)]}, format="json")
        self.assertEqual(r.data["estimated_total_minor"], 500000)
        self.assertEqual(self.api.post(url + "run-now/").status_code, 201)
        self.assertEqual(self.api.patch(url, {"frequency": "weekly"}, format="json").status_code, 400)  # schedule is locked now
        self.assertEqual(self.api.patch(url, {"name": "Still editable"}, format="json").status_code, 200)

    def test_list_filter(self):
        a = self.make_rec()
        self.make_rec()
        self.api.post(f"/api/recurring-invoices/{a['id']}/pause/")
        self.assertEqual(self.api.get("/api/recurring-invoices/", {"status": "paused"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/recurring-invoices/", {"status": "nonsense"}).status_code, 400)


class RecurringRunTests(RecurringBase):
    def test_runs_create_linked_drafts_and_advance_by_the_month(self):
        rec = self.make_rec()
        self.assertEqual(self.generate(), 1)
        self.assertEqual(self.generate(), 0)                                      # not due again today
        stored = self.rec(rec["id"])
        self.assertEqual((stored.runs_count, stored.next_run_date), (1, add_months(self.today, 1)))
        invoice = self.generated(rec["id"])[0]
        self.assertEqual((invoice.status, invoice.client_id, invoice.total_minor), ("draft", rec["client"], 1180000))
        self.assertEqual(self.generate(add_months(self.today, 1)), 1)
        self.assertEqual(self.api.get(f"/api/recurring-invoices/{rec['id']}/").data["invoice_count"], 2)

    def test_weekly_every_two_weeks(self):
        rec = self.make_rec(frequency="weekly", interval=2)
        self.generate()
        self.assertEqual(self.rec(rec["id"]).next_run_date, self.today + timedelta(days=14))

    def test_max_runs_ends_the_schedule(self):
        rec = self.make_rec(max_runs=2)
        self.generate()
        self.assertEqual(self.rec(rec["id"]).status, "active")
        self.generate(add_months(self.today, 1))
        self.assertEqual(self.rec(rec["id"]).status, "ended")
        self.assertEqual(self.generate(add_months(self.today, 2)), 0)

    def test_end_date_ends_the_schedule(self):
        rec = self.make_rec(end_date=(self.today + timedelta(days=40)).isoformat())
        self.generate()
        self.assertEqual(self.rec(rec["id"]).status, "active")
        self.generate(add_months(self.today, 1))
        self.assertEqual(self.rec(rec["id"]).status, "ended")

    def test_auto_send_sends_and_emails(self):
        rec = self.make_rec(auto_send=True)
        self.generate()
        invoice = self.generated(rec["id"])[0]
        self.assertEqual((invoice.status, invoice.number), ("sent", "INV-0001"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["retainer@co.test"])

    def test_auto_send_stopped_by_the_plan_limit_leaves_a_draft(self):
        self.set_limits("pro", max_invoices_per_month=0)
        rec = self.make_rec(auto_send=True)
        self.generate()
        invoice = self.generated(rec["id"])[0]
        stored = self.rec(rec["id"])
        self.assertEqual(invoice.status, "draft")
        self.assertTrue(stored.last_error.startswith("Created as a draft, not sent"), stored.last_error)
        self.assertEqual(stored.runs_count, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_an_archived_client_pauses_the_schedule(self):
        rec = self.make_rec()
        self.api.post(f"/api/clients/{rec['client']}/archive/")
        self.assertEqual(self.generate(), 0)
        stored = self.rec(rec["id"])
        self.assertEqual(stored.status, "paused")
        self.assertIn("archived", stored.last_error)
        self.assertEqual(self.generated(rec["id"]), [])

    def test_pause_and_resume(self):
        rec = self.make_rec()
        url = f"/api/recurring-invoices/{rec['id']}/"
        self.assertEqual(self.api.post(url + "pause/").data["status"], "paused")
        self.assertEqual(self.api.post(url + "pause/").status_code, 400)
        self.assertEqual(self.generate(), 0)                                      # paused: nothing runs
        self.assertEqual(self.api.post(url + "resume/").data["status"], "active")
        self.assertEqual(self.api.post(url + "resume/").status_code, 400)

    def test_resuming_after_a_long_pause_does_not_backdate_a_pile_of_invoices(self):
        rec = self.make_rec()
        self.api.post(f"/api/recurring-invoices/{rec['id']}/pause/")
        future = self.today + timedelta(days=90)
        with self.in_tenant():
            services.resume_recurring(RecurringInvoice.objects.get(pk=rec["id"]), today=future)
        self.assertEqual(self.rec(rec["id"]).next_run_date, future)
        self.assertEqual(self.generate(future), 1)
        self.assertEqual(self.rec(rec["id"]).next_run_date, add_months(future, 1))   # continues from the new anchor
        self.assertEqual(self.generate(future), 0)

    def test_run_now(self):
        rec = self.make_rec(start_date=(self.today + timedelta(days=10)).isoformat())
        r = self.api.post(f"/api/recurring-invoices/{rec['id']}/run-now/")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "draft")
        stored = self.rec(rec["id"])
        self.assertEqual((stored.runs_count, stored.next_run_date), (1, add_months(self.today + timedelta(days=10), 1)))
        self.api.post(f"/api/recurring-invoices/{rec['id']}/pause/")
        self.assertEqual(self.api.post(f"/api/recurring-invoices/{rec['id']}/run-now/").status_code, 400)

    def test_one_failing_schedule_does_not_stop_the_others(self):
        self.make_rec(self.emailed_client("One Co", "one@co.test"))
        self.make_rec(self.emailed_client("Two Co", "two@co.test"))
        with patch.object(services, "generate_next", side_effect=[RuntimeError("boom"), object()]):
            self.assertEqual(self.generate(), 1)


class SchedulerTests(RecurringBase):
    def test_daily_jobs_run_once_per_local_day_after_nine(self):
        early = datetime(2026, 10, 7, 4, 0, tzinfo=dt_utc.utc)
        later = datetime(2026, 10, 7, 10, 0, tzinfo=dt_utc.utc)
        with self.in_tenant():
            self.assertIsNone(services.run_daily_jobs_if_due(now=early))                    # 04:00 UTC: too early
            self.assertEqual(services.run_daily_jobs_if_due(now=later), {"recurring_created": 0, "reminders_queued": 0})
            self.assertEqual(InvoiceSettings.load().last_daily_run_on, date(2026, 10, 7))
            self.assertIsNone(services.run_daily_jobs_if_due(now=later + timedelta(hours=3)))  # already ran today
            self.assertIsNotNone(services.run_daily_jobs_if_due(now=later + timedelta(days=1)))
            self.assertIsNotNone(services.run_daily_jobs_if_due(now=later, force=True))

    def test_nine_oclock_means_the_organizations_nine_oclock(self):
        with self.in_tenant():
            cfg = InvoiceSettings.load()
            cfg.timezone = "Asia/Kolkata"
            cfg.save()
            self.assertIsNone(services.run_daily_jobs_if_due(now=datetime(2026, 10, 7, 3, 0, tzinfo=dt_utc.utc)))   # 08:30 in India
            self.assertIsNotNone(services.run_daily_jobs_if_due(now=datetime(2026, 10, 7, 4, 0, tzinfo=dt_utc.utc)))  # 09:30 in India

    def test_daily_jobs_create_due_recurring_invoices(self):
        rec = self.make_rec()
        noon = datetime.combine(self.today, time(12), tzinfo=dt_utc.utc)
        with self.in_tenant():
            result = services.run_daily_jobs_if_due(now=noon)
        self.assertEqual(result["recurring_created"], 1)
        self.assertEqual(self.rec(rec["id"]).runs_count, 1)

    def test_the_hourly_task_visits_organizations_but_not_the_public_tenant(self):
        with patch.object(services, "run_daily_jobs_if_due", return_value={"ok": 1}):
            results = tasks.run_scheduled_jobs()
        self.assertEqual(results.get(self.tenant.schema_name), {"ok": 1})
        self.assertNotIn("public", results)

    def test_the_manual_command(self):
        self.make_rec()
        out = StringIO()
        call_command("run_scheduled", subdomain="acme", force=True, stdout=out)
        self.assertIn("recurring_created", out.getvalue())
        self.assertEqual(self.api.get("/api/invoices/").data["count"], 1)
