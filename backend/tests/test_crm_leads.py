from apps.tenants.models import Role

from .test_crm_clients import CrmBase


class LeadTests(CrmBase):
    def make_lead(self, **extra):
        data = {
            "title": "Website redesign", "contact_name": "Priya", "contact_email": "priya@xco.test",
            "company_name": "Xco", "value_minor": 5000000,
        }
        r = self.api.post("/api/leads/", {**data, **extra}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def move(self, lead_id, stage, api=None):
        return (api or self.api).post(f"/api/leads/{lead_id}/move/", {"stage": stage}, format="json")

    def test_stage_moves_stamp_closed_at_and_log(self):
        lead = self.make_lead()
        lid = lead["id"]
        self.assertEqual(lead["stage"], "lead")

        r = self.move(lid, "proposal")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["closed_at"])

        r = self.move(lid, "won")
        self.assertIsNotNone(r.data["closed_at"])

        r = self.move(lid, "lead")  # reopened
        self.assertIsNone(r.data["closed_at"])

        self.assertEqual(self.move(lid, "banana").status_code, 400)

        kinds = self.kinds(f"/api/leads/{lid}/timeline/")
        self.assertEqual(kinds.count("stage_changed"), 3)
        self.assertEqual(kinds[-1], "lead_created")

    def test_moving_to_same_stage_is_a_noop(self):
        lid = self.make_lead()["id"]
        self.move(lid, "lead")
        self.assertEqual(self.kinds(f"/api/leads/{lid}/timeline/"), ["lead_created"])

    def test_patching_stage_also_logs(self):
        lid = self.make_lead()["id"]
        r = self.api.patch(f"/api/leads/{lid}/", {"stage": "lost"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.data["closed_at"])
        self.assertIn("stage_changed", self.kinds(f"/api/leads/{lid}/timeline/"))

    def test_validation(self):
        self.assertEqual(self.api.post("/api/leads/", {"title": "X", "value_minor": -5}, format="json").status_code, 400)
        self.assertEqual(self.api.post("/api/leads/", {"title": "X", "currency": "RUPEES"}, format="json").status_code, 400)
        r = self.api.post("/api/leads/", {"title": "X", "currency": "usd"}, format="json")
        self.assertEqual(r.data["currency"], "USD")

    def test_filters(self):
        a = self.make_lead(title="Alpha project")
        self.make_lead(title="Beta project")
        self.move(a["id"], "won")
        self.assertEqual(self.api.get("/api/leads/", {"stage": "won"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/leads/", {"stage": "open"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/leads/", {"q": "beta"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/leads/", {"stage": "nope"}).status_code, 400)

    def test_convert_creates_client_contact_and_history(self):
        lead = self.make_lead()
        self.move(lead["id"], "contacted")

        r = self.api.post(f"/api/leads/{lead['id']}/convert/")
        self.assertEqual(r.status_code, 201, r.data)
        client = r.data["client"]
        self.assertEqual(client["name"], "Xco")
        self.assertEqual(r.data["lead"]["stage"], "won")
        self.assertEqual(r.data["lead"]["client"], client["id"])

        contacts = self.api.get(f"/api/clients/{client['id']}/contacts/").data
        self.assertEqual([(c["name"], c["is_primary"]) for c in contacts], [("Priya", True)])

        # The client's timeline includes what happened to the lead BEFORE it became a client.
        kinds = self.kinds(f"/api/clients/{client['id']}/timeline/")
        for expected in ("lead_created", "stage_changed", "lead_converted", "client_created"):
            self.assertIn(expected, kinds)

        # Converting twice is refused
        self.assertEqual(self.api.post(f"/api/leads/{lead['id']}/convert/").status_code, 400)

    def test_convert_into_existing_client(self):
        existing = self.make_client("Existing Co")
        lead = self.make_lead()
        r = self.api.post(f"/api/leads/{lead['id']}/convert/", {"client_id": existing["id"]}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["client"]["id"], existing["id"])
        self.assertEqual(self.api.get("/api/clients/", {"status": "all"}).data["count"], 1)  # no duplicate

        other = self.make_lead(title="Other")
        r = self.api.post(f"/api/leads/{other['id']}/convert/", {"client_id": 9999}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_roles_on_leads(self):
        lead = self.make_lead()
        lid = lead["id"]
        viewer = self.as_role(Role.VIEWER)
        accountant = self.as_role(Role.ACCOUNTANT)
        admin = self.as_role(Role.ADMIN)

        self.assertEqual(viewer.get("/api/leads/").status_code, 200)
        self.assertEqual(self.move(lid, "contacted", viewer).status_code, 403)
        self.assertEqual(viewer.post(f"/api/leads/{lid}/convert/").status_code, 403)
        self.assertEqual(self.move(lid, "contacted", accountant).status_code, 200)
        self.assertEqual(accountant.delete(f"/api/leads/{lid}/").status_code, 403)
        self.assertEqual(admin.delete(f"/api/leads/{lid}/").status_code, 204)

    def test_board_groups_by_stage(self):
        a = self.make_lead(title="A", value_minor=1000)
        b = self.make_lead(title="B", value_minor=2500)
        self.move(b["id"], "lost")
        r = self.api.get("/api/leads/board/")
        self.assertEqual(r.status_code, 200)
        stages = {s["stage"]: s for s in r.data["stages"]}
        self.assertEqual(list(stages), ["lead", "contacted", "proposal", "won", "lost"])
        self.assertEqual((stages["lead"]["count"], stages["lead"]["value_minor"]), (1, 1000))
        self.assertEqual((stages["lost"]["count"], stages["lost"]["value_minor"]), (1, 2500))
        self.assertEqual(stages["won"]["leads"], [])
        self.assertEqual(stages["lead"]["leads"][0]["id"], a["id"])


class StatsTests(CrmBase):
    def test_stats(self):
        r = self.api.get("/api/crm/stats/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["active_clients"], 0)
        self.assertIsNone(r.data["conversion_rate"])

        won = self.api.post("/api/leads/", {"title": "Won one", "company_name": "Xco", "value_minor": 5000000}, format="json").data
        lost = self.api.post("/api/leads/", {"title": "Lost one", "value_minor": 2000000}, format="json").data
        open_ = self.api.post("/api/leads/", {"title": "Still open", "value_minor": 700}, format="json").data
        self.api.post(f"/api/leads/{won['id']}/convert/")
        self.api.post(f"/api/leads/{lost['id']}/move/", {"stage": "lost"}, format="json")
        archived = self.make_client("To archive")
        self.api.post(f"/api/clients/{archived['id']}/archive/")

        r = self.api.get("/api/crm/stats/")
        self.assertEqual(r.data["active_clients"], 1)       # the one created by converting the lead
        self.assertEqual(r.data["archived_clients"], 1)
        self.assertEqual(r.data["won_leads"], 1)
        self.assertEqual(r.data["won_value_minor"], 5000000)
        self.assertEqual(r.data["lost_leads"], 1)
        self.assertEqual(r.data["conversion_rate"], 0.5)
        self.assertEqual(r.data["open_leads"], 1)
        self.assertEqual(r.data["open_pipeline_value_minor"], 700)
        self.assertEqual(r.data["leads_by_stage"]["lost"], 1)
        self.assertEqual(open_["stage"], "lead")

    def test_everyone_can_read_stats_but_outsiders_cannot(self):
        self.assertEqual(self.as_role(Role.VIEWER).get("/api/crm/stats/").status_code, 200)
        from .helpers import client_for, make_user
        outsider = make_user("outsider@example.test")
        self.assertEqual(client_for(outsider, self.host).get("/api/crm/stats/").status_code, 403)
