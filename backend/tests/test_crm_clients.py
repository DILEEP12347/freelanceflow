from apps.tenants.models import Role

from .helpers import TenantTestBase, client_for


class CrmBase(TenantTestBase):
    def setUp(self):
        super().setUp()
        self.api = client_for(self.owner, self.host)

    def as_role(self, role, email=None):
        user = self.add_member(email or f"{role.value}@acme.test", role)
        return client_for(user, self.host)

    def make_client(self, name="Alpha Co", **extra):
        r = self.api.post("/api/clients/", {"name": name, **extra}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def kinds(self, url):
        r = self.api.get(url)
        self.assertEqual(r.status_code, 200, r.data)
        return [a["kind"] for a in r.data["results"]]


class ClientTests(CrmBase):
    def test_role_rules(self):
        cid = self.make_client()["id"]
        url = f"/api/clients/{cid}/"
        viewer = self.as_role(Role.VIEWER)
        accountant = self.as_role(Role.ACCOUNTANT)
        admin = self.as_role(Role.ADMIN)

        self.assertEqual(viewer.get(url).status_code, 200)
        self.assertEqual(viewer.patch(url, {"phone": "1"}, format="json").status_code, 403)
        self.assertEqual(accountant.patch(url, {"phone": "123"}, format="json").status_code, 200)
        self.assertEqual(accountant.delete(url).status_code, 403)   # only owner/admin may delete
        self.assertEqual(admin.delete(url).status_code, 204)

    def test_status_cannot_be_set_directly(self):
        cid = self.make_client()["id"]
        r = self.api.patch(f"/api/clients/{cid}/", {"status": "archived"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "active")  # ignored: use /archive/

    def test_search_status_filter_and_pagination(self):
        alpha = self.make_client("Alpha Co", email="hello@alpha.test")
        self.make_client("Beta LLC")
        self.make_client("Gamma Ltd")

        r = self.api.get("/api/clients/", {"q": "alpha"})
        self.assertEqual(r.data["count"], 1)
        r = self.api.get("/api/clients/", {"q": "hello@alpha"})
        self.assertEqual(r.data["count"], 1)

        self.assertEqual(self.api.post(f"/api/clients/{alpha['id']}/archive/").status_code, 200)
        self.assertEqual(self.api.get("/api/clients/").data["count"], 2)                       # default: active
        self.assertEqual(self.api.get("/api/clients/", {"status": "archived"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/clients/", {"status": "all"}).data["count"], 3)
        self.assertEqual(self.api.get("/api/clients/", {"status": "nonsense"}).status_code, 400)

        r = self.api.get("/api/clients/", {"status": "all", "page_size": 2})
        self.assertEqual(len(r.data["results"]), 2)
        self.assertIsNotNone(r.data["next"])

        r = self.api.get("/api/clients/", {"status": "all", "ordering": "name"})
        self.assertEqual([c["name"] for c in r.data["results"]], ["Alpha Co", "Beta LLC", "Gamma Ltd"])

    def test_archive_then_restore_works_on_archived_client(self):
        cid = self.make_client()["id"]
        self.api.post(f"/api/clients/{cid}/archive/")
        # an archived client can still be opened directly
        self.assertEqual(self.api.get(f"/api/clients/{cid}/").data["status"], "archived")
        r = self.api.post(f"/api/clients/{cid}/restore/")
        self.assertEqual(r.data["status"], "active")

    def test_viewer_cannot_archive(self):
        cid = self.make_client()["id"]
        self.assertEqual(self.as_role(Role.VIEWER).post(f"/api/clients/{cid}/archive/").status_code, 403)


class TagTests(CrmBase):
    def test_tags_and_filtering(self):
        r = self.api.post("/api/tags/", {"name": "VIP", "color": "#FF0000"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["color"], "#ff0000")
        tid = r.data["id"]

        self.assertEqual(self.api.post("/api/tags/", {"name": "vip"}, format="json").status_code, 400)
        self.assertEqual(self.api.post("/api/tags/", {"name": "Bad", "color": "red"}, format="json").status_code, 400)

        tagged = self.make_client("Tagged", tag_ids=[tid])
        self.assertEqual(tagged["tags"][0]["name"], "VIP")
        self.make_client("Untagged")

        self.assertEqual(self.api.get("/api/clients/", {"tag": "vip"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/clients/", {"tag": str(tid)}).data["count"], 1)

    def test_viewer_cannot_create_tags(self):
        r = self.as_role(Role.VIEWER).post("/api/tags/", {"name": "X"}, format="json")
        self.assertEqual(r.status_code, 403)


class ContactAndNoteTests(CrmBase):
    def test_only_one_primary_contact(self):
        cid = self.make_client()["id"]
        url = f"/api/clients/{cid}/contacts/"
        self.assertEqual(self.api.post(url, {"name": "A", "is_primary": True}, format="json").status_code, 201)
        self.assertEqual(self.api.post(url, {"name": "B", "is_primary": True}, format="json").status_code, 201)
        primaries = [c["name"] for c in self.api.get(url).data if c["is_primary"]]
        self.assertEqual(primaries, ["B"])

    def test_contacts_on_missing_client_404(self):
        self.assertEqual(self.api.get("/api/clients/9999/contacts/").status_code, 404)
        self.assertEqual(self.api.post("/api/clients/9999/contacts/", {"name": "A"}, format="json").status_code, 404)

    def test_viewer_cannot_add_contact(self):
        cid = self.make_client()["id"]
        r = self.as_role(Role.VIEWER).post(f"/api/clients/{cid}/contacts/", {"name": "A"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_note_delete_rules(self):
        cid = self.make_client()["id"]
        url = f"/api/clients/{cid}/notes/"
        acct1 = self.as_role(Role.ACCOUNTANT, "acct1@acme.test")
        acct2 = self.as_role(Role.ACCOUNTANT, "acct2@acme.test")
        admin = self.as_role(Role.ADMIN)

        r = acct1.post(url, {"body": "Called, wants a quote"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["author_email"], "acct1@acme.test")
        note_url = f"{url}{r.data['id']}/"

        self.assertEqual(acct2.delete(note_url).status_code, 403)   # not the author
        self.assertEqual(acct1.delete(note_url).status_code, 204)   # author

        other = acct1.post(url, {"body": "Second note"}, format="json").data["id"]
        self.assertEqual(admin.delete(f"{url}{other}/").status_code, 204)  # managers can delete any

    def test_empty_note_rejected_and_viewer_can_read(self):
        cid = self.make_client()["id"]
        url = f"/api/clients/{cid}/notes/"
        self.assertEqual(self.api.post(url, {"body": "   "}, format="json").status_code, 400)
        self.assertEqual(self.as_role(Role.VIEWER).get(url).status_code, 200)
        self.assertEqual(self.as_role(Role.VIEWER, "v2@acme.test").post(url, {"body": "hi"}, format="json").status_code, 403)


class TimelineTests(CrmBase):
    def test_client_timeline_records_events_newest_first(self):
        cid = self.make_client()["id"]
        tl = f"/api/clients/{cid}/timeline/"
        self.assertEqual(self.kinds(tl), ["client_created"])

        self.api.post(f"/api/clients/{cid}/notes/", {"body": "Kickoff call"}, format="json")
        self.assertEqual(self.kinds(tl)[0], "note_added")

        self.api.post(f"/api/clients/{cid}/contacts/", {"name": "Priya"}, format="json")
        self.assertEqual(self.kinds(tl)[0], "contact_added")

        self.api.post(f"/api/clients/{cid}/archive/")
        self.assertEqual(self.kinds(tl)[0], "client_archived")
        self.api.post(f"/api/clients/{cid}/restore/")
        self.assertEqual(self.kinds(tl)[0], "client_restored")
        self.assertEqual(len(self.kinds(tl)), 5)

    def test_viewer_can_read_timeline(self):
        cid = self.make_client()["id"]
        r = self.as_role(Role.VIEWER).get(f"/api/clients/{cid}/timeline/")
        self.assertEqual(r.status_code, 200)
