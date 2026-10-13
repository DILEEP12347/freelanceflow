from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.work import services as work_services
from apps.work.models import Project
from apps.tenants.models import Role

from .test_week6_api import Week6Base


class Week7Base(Week6Base):
    def make_project(self, client_id=None, rate=150000, api=None, **extra):
        client_id = client_id or self.make_client("Project Co")["id"]
        body = {"name": "Website", "client": client_id, "hourly_rate_minor": rate, **extra}
        r = (api or self.api).post("/api/projects/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def add_time(self, project_id, minutes=60, hours_ago=5, api=None, **extra):
        started = (timezone.now() - timedelta(hours=hours_ago)).isoformat()
        body = {"project": project_id, "started_at": started, "duration_minutes": minutes, **extra}
        r = (api or self.api).post("/api/time/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def make_expense(self, api=None, **extra):
        body = {"description": "Domain", "amount_minor": 118000, "tax_minor": 18000,
                "incurred_on": timezone.localdate().isoformat(), **extra}
        r = (api or self.api).post("/api/expenses/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def work_invoice(self, client_id, time_ids=(), expense_ids=(), api=None, **extra):
        body = {"client": client_id, "time_entry_ids": list(time_ids), "expense_ids": list(expense_ids), **extra}
        return (api or self.api).post("/api/work/invoice/", body, format="json")

    def start(self, project_id, api=None, **extra):
        return (api or self.api).post("/api/time/start/", {"project": project_id, **extra}, format="json")

    def get_entry(self, entry_id):
        return self.api.get(f"/api/time/{entry_id}/").data

    def timer_via_service(self, project_id, minutes_ago, user=None):
        with self.in_tenant():
            project = Project.objects.get(pk=project_id)
            entry, _ = work_services.start_timer(user or self.owner, project, now=timezone.now() - timedelta(minutes=minutes_ago))
        return entry


class ProjectTests(Week7Base):
    def test_create_list_and_filter(self):
        a, b = self.make_client("Alpha")["id"], self.make_client("Beta")["id"]
        first = self.make_project(a, name="Alpha site")
        self.make_project(b, name="Beta app", rate=0)
        self.assertEqual((first["status"], first["currency"], first["client_name"]), ("active", "INR", "Alpha"))
        self.assertEqual(self.api.get("/api/projects/").data["count"], 2)
        self.assertEqual(self.api.get("/api/projects/", {"client": a}).data["count"], 1)
        self.assertEqual(self.api.get("/api/projects/", {"q": "beta"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/projects/", {"status": "archived"}).data["count"], 0)
        self.assertEqual(self.api.get("/api/projects/", {"status": "nonsense"}).status_code, 400)

    def test_archive_restore_and_the_timer(self):
        project = self.make_project()
        url = f"/api/projects/{project['id']}/"
        self.assertEqual(self.api.post(url + "archive/").data["status"], "archived")
        self.assertEqual(self.start(project["id"]).status_code, 400)               # no new time on an archived project
        self.assertEqual(self.api.post(url + "restore/").data["status"], "active")
        self.assertEqual(self.start(project["id"]).status_code, 201)

    def test_delete_rules(self):
        used, empty = self.make_project(), self.make_project(name="Unused")
        self.add_time(used["id"])
        self.assertEqual(self.api.delete(f"/api/projects/{used['id']}/").status_code, 409)       # has time: archive instead
        self.assertEqual(self.as_role(Role.ACCOUNTANT).delete(f"/api/projects/{empty['id']}/").status_code, 403)
        self.assertEqual(self.api.delete(f"/api/projects/{empty['id']}/").status_code, 204)

    def test_archived_clients_cannot_get_or_keep_active_projects(self):
        cid = self.make_client("Soon Archived")["id"]
        project = self.make_project(cid)
        self.api.post(f"/api/projects/{project['id']}/archive/")
        self.api.post(f"/api/clients/{cid}/archive/")
        self.assertEqual(self.api.post("/api/projects/", {"name": "New", "client": cid}, format="json").status_code, 400)
        self.assertEqual(self.api.post(f"/api/projects/{project['id']}/restore/").status_code, 400)

    def test_validation(self):
        cid = self.make_client("Val Co")["id"]
        for extra in ({"hourly_rate_minor": -1}, {"currency": "RUPEES"}, {"name": ""}):
            r = self.api.post("/api/projects/", {"name": "P", "client": cid, **extra}, format="json")
            self.assertEqual(r.status_code, 400, extra)

    def test_client_and_currency_are_locked_once_time_is_logged(self):
        project = self.make_project()
        other = self.make_client("Someone Else")["id"]
        url = f"/api/projects/{project['id']}/"
        self.assertEqual(self.api.patch(url, {"client": other}, format="json").status_code, 200)   # nothing logged yet
        self.add_time(project["id"])
        self.assertEqual(self.api.patch(url, {"client": project["client"]}, format="json").status_code, 400)
        self.assertEqual(self.api.patch(url, {"currency": "USD"}, format="json").status_code, 400)
        self.assertEqual(self.api.patch(url, {"name": "Renamed", "hourly_rate_minor": 200000}, format="json").status_code, 200)

    def test_roles(self):
        viewer = self.as_role(Role.VIEWER)
        self.assertEqual(viewer.get("/api/projects/").status_code, 200)
        self.assertEqual(viewer.post("/api/projects/", {"name": "x", "client": 1}, format="json").status_code, 403)


class TimerTests(Week7Base):
    def test_start_running_and_stop(self):
        project = self.make_project(rate=150000)
        r = self.start(project["id"], description="Homepage")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["is_running"], r.data["hourly_rate_minor"], r.data["billable"], r.data["ended_at"]),
                         (True, 150000, True, None))
        self.assertIsNone(r.data["stopped_previous"])
        self.assertEqual(self.api.get("/api/time/running/").data["running"]["id"], r.data["id"])
        stopped = self.api.post(f"/api/time/{r.data['id']}/stop/")
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.data["is_running"])
        self.assertIsNone(self.api.get("/api/time/running/").data["running"])

    def test_duration_hours_and_money(self):
        project = self.make_project(rate=150000)
        entry = self.timer_via_service(project["id"], minutes_ago=120)
        with self.in_tenant():
            work_services.stop_timer(entry, now=entry.started_at + timedelta(minutes=90))
        data = self.get_entry(entry.pk)
        self.assertEqual((data["duration_seconds"], data["hours"], data["amount_minor"]), (5400, "1.50", 225000))

    def test_a_running_timer_shows_the_time_so_far(self):
        project = self.make_project()
        self.timer_via_service(project["id"], minutes_ago=30)
        self.assertGreaterEqual(self.api.get("/api/time/running/").data["running"]["duration_seconds"], 1790)

    def test_the_rate_is_copied_so_later_rate_changes_do_not_rewrite_history(self):
        project = self.make_project(rate=150000)
        entry = self.add_time(project["id"])
        self.api.patch(f"/api/projects/{project['id']}/", {"hourly_rate_minor": 999000}, format="json")
        self.assertEqual(self.get_entry(entry["id"])["hourly_rate_minor"], 150000)
        self.assertEqual(self.add_time(project["id"])["hourly_rate_minor"], 999000)

    def test_starting_another_timer_stops_the_first(self):
        p1, p2 = self.make_project(name="One"), self.make_project(name="Two")
        first = self.start(p1["id"])
        second = self.start(p2["id"])
        self.assertEqual(second.data["stopped_previous"], first.data["id"])
        self.assertEqual(self.api.get("/api/time/", {"running": "true"}).data["count"], 1)
        self.assertFalse(self.get_entry(first.data["id"])["is_running"])

    def test_every_person_has_their_own_timer(self):
        project = self.make_project()
        self.start(project["id"])
        other = self.start(project["id"], api=self.as_role(Role.ACCOUNTANT))
        self.assertIsNone(other.data["stopped_previous"])
        self.assertEqual(self.api.get("/api/time/", {"running": "true"}).data["count"], 2)

    def test_stop_rules(self):
        project = self.make_project()
        mine = self.start(project["id"]).data["id"]
        self.assertEqual(self.api.post(f"/api/time/{mine}/stop/").status_code, 200)
        self.assertEqual(self.api.post(f"/api/time/{mine}/stop/").status_code, 400)           # already stopped
        again = self.start(project["id"]).data["id"]
        self.assertEqual(self.as_role(Role.ACCOUNTANT).post(f"/api/time/{again}/stop/").status_code, 403)
        self.assertEqual(self.as_role(Role.ADMIN).post(f"/api/time/{again}/stop/").status_code, 200)  # managers may

    def test_viewers_cannot_track_time(self):
        project = self.make_project()
        self.assertEqual(self.start(project["id"], api=self.as_role(Role.VIEWER)).status_code, 403)


class ManualEntryTests(Week7Base):
    def test_by_minutes_and_by_end_time(self):
        project = self.make_project(rate=150000)
        data = self.add_time(project["id"], minutes=60)
        self.assertEqual((data["duration_seconds"], data["hours"], data["amount_minor"], data["is_running"]), (3600, "1.00", 150000, False))
        start = timezone.now() - timedelta(hours=3)
        r = self.api.post("/api/time/", {"project": project["id"], "started_at": start.isoformat(),
                                         "ended_at": (start + timedelta(minutes=45)).isoformat()}, format="json")
        self.assertEqual((r.status_code, r.data["duration_seconds"]), (201, 2700))

    def test_validation(self):
        project = self.make_project()
        now = timezone.now()
        base = {"project": project["id"], "started_at": (now - timedelta(hours=3)).isoformat()}
        bad = [
            {},                                                                              # neither end nor minutes
            {"duration_minutes": 30, "ended_at": now.isoformat()},                            # both
            {"ended_at": (now - timedelta(hours=4)).isoformat()},                             # ends before it starts
            {"started_at": (now - timedelta(hours=40)).isoformat(), "ended_at": (now - timedelta(hours=10)).isoformat()},  # 30 h
            {"started_at": (now + timedelta(hours=1)).isoformat(), "duration_minutes": 30},   # in the future
            {"duration_minutes": 0},
        ]
        for extra in bad:
            r = self.api.post("/api/time/", {**base, **extra}, format="json")
            self.assertEqual(r.status_code, 400, extra)

    def test_billable_and_rate_can_be_overridden(self):
        project = self.make_project(rate=150000)
        data = self.add_time(project["id"], billable=False, hourly_rate_minor=99900)
        self.assertEqual((data["billable"], data["hourly_rate_minor"]), (False, 99900))

    def test_updating_entries(self):
        project = self.make_project()
        entry = self.add_time(project["id"])
        url = f"/api/time/{entry['id']}/"
        r = self.api.patch(url, {"description": "Edited", "billable": False, "hourly_rate_minor": 120000}, format="json")
        self.assertEqual((r.status_code, r.data["description"], r.data["billable"], r.data["hourly_rate_minor"]), (200, "Edited", False, 120000))
        new_end = parse_datetime(r.data["started_at"]) + timedelta(hours=2)
        r = self.api.patch(url, {"ended_at": new_end.isoformat()}, format="json")
        self.assertEqual(r.data["duration_seconds"], 7200)                                 # recomputed from the times
        running = self.start(project["id"]).data["id"]
        self.assertEqual(self.api.patch(f"/api/time/{running}/", {"started_at": new_end.isoformat()}, format="json").status_code, 400)

    def test_only_your_own_entries_unless_you_manage(self):
        project = self.make_project()
        entry = self.add_time(project["id"])
        url = f"/api/time/{entry['id']}/"
        self.assertEqual(self.as_role(Role.ACCOUNTANT).patch(url, {"description": "x"}, format="json").status_code, 403)
        self.assertEqual(self.as_role(Role.ADMIN).patch(url, {"description": "by admin"}, format="json").status_code, 200)

    def test_delete_rules(self):
        project = self.make_project()
        mine, theirs = self.add_time(project["id"]), self.add_time(project["id"])
        self.assertEqual(self.as_role(Role.ACCOUNTANT).delete(f"/api/time/{theirs['id']}/").status_code, 403)
        self.assertEqual(self.api.delete(f"/api/time/{mine['id']}/").status_code, 204)

    def test_filters_and_summary(self):
        a, b = self.make_client("Client A")["id"], self.make_client("Client B")["id"]
        p1, p2 = self.make_project(a, rate=150000, name="P1"), self.make_project(b, rate=100000, name="P2")
        self.add_time(p1["id"], 60, hours_ago=6)
        self.add_time(p1["id"], 30, hours_ago=4)
        self.add_time(p2["id"], 120, hours_ago=3, billable=False)
        data = self.api.get("/api/time/").data
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["summary"], {"total_seconds": 12600, "billable_seconds": 5400, "unbilled_seconds": 5400, "unbilled_value_minor": 225000})
        get = lambda **p: self.api.get("/api/time/", p).data["count"]  # noqa: E731
        self.assertEqual((get(project=p1["id"]), get(client=b), get(billable="false"), get(user="me"), get(billed="true"), get(running="true")),
                         (2, 1, 1, 3, 0, 0))
        self.assertEqual(self.api.get("/api/time/", {"billable": "maybe"}).status_code, 400)
        self.assertEqual(self.api.get("/api/time/", {"from": "garbage"}).status_code, 400)

    def test_date_range_filter(self):
        project = self.make_project()
        for day in ("2026-09-10", "2026-09-12"):
            self.api.post("/api/time/", {"project": project["id"], "started_at": f"{day}T10:00:00+00:00", "duration_minutes": 60}, format="json")
        get = lambda **p: self.api.get("/api/time/", p).data["count"]  # noqa: E731
        self.assertEqual((get(**{"from": "2026-09-10", "to": "2026-09-10"}), get(**{"from": "2026-09-10", "to": "2026-09-11"}),
                          get(**{"from": "2026-09-01", "to": "2026-09-30"}), get(**{"from": "2099-01-01"})), (1, 1, 2, 0))


class ExpenseTests(Week7Base):
    def test_create_defaults_and_validation(self):
        data = self.make_expense()
        self.assertEqual((data["currency"], data["ex_tax_minor"], data["created_by_email"]), ("INR", 100000, self.owner.email))
        cid = self.make_client("Exp Co")["id"]
        project = self.make_project(self.make_client("Other Co")["id"])
        bad = [{"tax_minor": 200000}, {"amount_minor": 0}, {"billable": True}, {"project": project["id"], "client": cid}, {"currency": "XX"}]
        for extra in bad:
            body = {"description": "Bad", "amount_minor": 118000, "incurred_on": "2026-09-01", **extra}
            self.assertEqual(self.api.post("/api/expenses/", body, format="json").status_code, 400, extra)

    def test_filters_summary_and_categories(self):
        a, b = self.make_client("Client A")["id"], self.make_client("Client B")["id"]
        pa = self.make_project(a)
        self.make_expense(category="Software", project=pa["id"], billable=True, incurred_on="2026-09-01")
        self.make_expense(description="Train", category="Travel", amount_minor=50000, tax_minor=0, client=b, incurred_on="2026-09-15")
        self.make_expense(description="Misc", category="Software", amount_minor=20000, tax_minor=0, incurred_on="2026-10-01")
        data = self.api.get("/api/expenses/").data
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["summary"], [{"currency": "INR", "amount_minor": 188000, "tax_minor": 18000, "count": 3}])
        get = lambda **p: self.api.get("/api/expenses/", p).data["count"]  # noqa: E731
        self.assertEqual((get(category="software"), get(client=a), get(client=b), get(project=pa["id"]), get(billable="true"), get(q="train")),
                         (2, 1, 1, 1, 1, 1))                         # a client filter also finds expenses through their project
        self.assertEqual(get(**{"from": "2026-09-10", "to": "2026-09-30"}), 1)
        self.assertEqual(self.api.get("/api/expenses/categories/").data, ["General", "Software", "Travel"])

    def test_billed_expenses_are_locked(self):
        cid = self.make_client("Bill Co")["id"]
        expense = self.make_expense(client=cid, billable=True)
        self.assertEqual(self.work_invoice(cid, expense_ids=[expense["id"]]).status_code, 201)
        url = f"/api/expenses/{expense['id']}/"
        self.assertEqual(self.api.patch(url, {"description": "x"}, format="json").status_code, 400)
        self.assertEqual(self.api.delete(url).status_code, 400)

    def test_roles(self):
        self.assertEqual(self.as_role(Role.VIEWER).post("/api/expenses/", {}, format="json").status_code, 403)
        accountant = self.as_role(Role.ACCOUNTANT)
        made = self.make_expense(api=accountant)
        self.assertEqual(accountant.delete(f"/api/expenses/{made['id']}/").status_code, 403)    # deleting is for managers
        self.assertEqual(self.api.delete(f"/api/expenses/{made['id']}/").status_code, 204)


class WorkInvoiceTests(Week7Base):
    def setUp(self):
        super().setUp()
        self.cid = self.make_client("Billable Co")["id"]
        self.project = self.make_project(self.cid, rate=150000, name="Website")

    def invoice_detail(self, response):
        return self.api.get(f"/api/invoices/{response.data['invoice_id']}/").data

    def test_time_becomes_one_line_per_project_and_the_entries_are_marked_billed(self):
        e1, e2 = self.add_time(self.project["id"], 60, hours_ago=6), self.add_time(self.project["id"], 30, hours_ago=4)
        r = self.work_invoice(self.cid, [e1["id"], e2["id"]], tax_rate_id=self.gst18)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["status"], r.data["time_entries"], r.data["total_minor"]), ("draft", 2, 265500))   # 1.5h x 1500 + 18%
        invoice = self.invoice_detail(r)
        self.assertEqual(len(invoice["lines"]), 1)
        line = invoice["lines"][0]
        self.assertEqual((line["quantity"], line["unit_price_minor"], line["tax_rate_bps"]), ("1.50", 150000, 1800))
        self.assertTrue(line["description"].startswith("Website - 1.50 h (2 entries)"))
        self.assertEqual(self.api.get("/api/time/", {"billed": "true"}).data["count"], 2)
        self.assertEqual(self.api.get("/api/time/", {"billed": "false"}).data["summary"]["unbilled_value_minor"], 0)
        again = self.work_invoice(self.cid, [e1["id"]])
        self.assertEqual(again.status_code, 400)
        self.assertIn("already on an invoice", " ".join(again.data["items"]))

    def test_one_line_per_entry_when_asked(self):
        e1, e2 = self.add_time(self.project["id"], 60, hours_ago=6), self.add_time(self.project["id"], 30, hours_ago=4)
        r = self.work_invoice(self.cid, [e1["id"], e2["id"]], group_by="entry")
        self.assertEqual(sorted(l["quantity"] for l in self.invoice_detail(r)["lines"]), ["0.50", "1.00"])

    def test_billable_expenses_are_passed_through_at_cost(self):
        entry = self.add_time(self.project["id"], 60)
        expense = self.make_expense(description="Domain", vendor="Namecheap", amount_minor=50000, tax_minor=0, project=self.project["id"], billable=True)
        r = self.work_invoice(self.cid, [entry["id"]], [expense["id"]])
        self.assertEqual((r.status_code, r.data["expenses"], r.data["total_minor"]), (201, 1, 200000))
        lines = self.invoice_detail(r)["lines"]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(l["description"] == "Expense: Domain (Namecheap)" and l["unit_price_minor"] == 50000 for l in lines))

    def test_every_problem_is_reported_at_once(self):
        other = self.make_project(self.make_client("Not Billable Co")["id"], name="Elsewhere")
        running = self.start(self.project["id"]).data["id"]
        no_bill = self.add_time(self.project["id"], billable=False)["id"]
        foreign = self.add_time(other["id"])["id"]
        not_rebillable = self.make_expense(client=self.cid)["id"]
        r = self.work_invoice(self.cid, [running, no_bill, foreign], [not_rebillable])
        self.assertEqual(r.status_code, 400)
        text = " ".join(r.data["items"])
        for phrase in ("still running", "not billable", "different client", "not marked billable"):
            self.assertIn(phrase, text)
        self.assertEqual(self.api.get("/api/invoices/").data["count"], 0)                 # nothing was created

    def test_different_currencies_cannot_share_an_invoice(self):
        usd = self.make_project(self.cid, name="Overseas", currency="USD")
        e1, e2 = self.add_time(self.project["id"]), self.add_time(usd["id"])
        r = self.work_invoice(self.cid, [e1["id"], e2["id"]])
        self.assertEqual(r.status_code, 400)
        self.assertIn("different currencies", " ".join(r.data["items"]))

    def test_foreign_currency_needs_an_exchange_rate(self):
        usd = self.make_project(self.cid, name="Overseas", currency="USD", rate=5000)
        entry = self.add_time(usd["id"])
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]]).status_code, 400)
        r = self.work_invoice(self.cid, [entry["id"]], exchange_rate="83.5")
        self.assertEqual((r.status_code, r.data["currency"]), (201, "USD"))

    def test_selection_rules(self):
        self.assertEqual(self.work_invoice(self.cid).status_code, 400)                      # nothing chosen
        self.assertEqual(self.work_invoice(self.cid, [999999]).status_code, 400)            # unknown id
        entry = self.add_time(self.project["id"])
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]], tax_rate_id=999999).status_code, 400)

    def test_an_archived_client_cannot_be_invoiced_and_nothing_is_marked_billed(self):
        entry = self.add_time(self.project["id"])
        self.api.post(f"/api/projects/{self.project['id']}/archive/")
        self.api.post(f"/api/clients/{self.cid}/archive/")
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]]).status_code, 400)
        self.assertEqual(self.api.get("/api/time/", {"billed": "true"}).data["count"], 0)

    def test_deleting_the_draft_gives_the_time_back(self):
        entry = self.add_time(self.project["id"])
        r = self.work_invoice(self.cid, [entry["id"]])
        self.assertEqual(self.api.delete(f"/api/invoices/{r.data['invoice_id']}/").status_code, 204)
        self.assertFalse(self.get_entry(entry["id"])["billed"])
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]]).status_code, 201)       # billable again

    def test_voiding_the_invoice_gives_time_and_expenses_back(self):
        entry = self.add_time(self.project["id"])
        expense = self.make_expense(client=self.cid, billable=True)
        r = self.work_invoice(self.cid, [entry["id"]], [expense["id"]])
        invoice_id = r.data["invoice_id"]
        self.assertEqual(self.send(invoice_id).status_code, 200)
        self.assertTrue(self.get_entry(entry["id"])["billed"])
        self.assertEqual(self.api.post(f"/api/invoices/{invoice_id}/void/", {}, format="json").status_code, 200)
        self.assertFalse(self.get_entry(entry["id"])["billed"])
        self.assertIsNone(self.api.get(f"/api/expenses/{expense['id']}/").data["invoice"])

    def test_billed_entries_are_locked(self):
        entry = self.add_time(self.project["id"])
        self.work_invoice(self.cid, [entry["id"]])
        url = f"/api/time/{entry['id']}/"
        self.assertEqual(self.api.patch(url, {"description": "x"}, format="json").status_code, 400)
        self.assertEqual(self.api.delete(url).status_code, 400)

    def test_roles(self):
        entry = self.add_time(self.project["id"])
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]], api=self.as_role(Role.VIEWER)).status_code, 403)
        self.assertEqual(self.work_invoice(self.cid, [entry["id"]], api=self.as_role(Role.ACCOUNTANT)).status_code, 201)
