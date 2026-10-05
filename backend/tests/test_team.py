import re
from datetime import timedelta

from django.core import mail
from django.utils import timezone

from apps.tenants.models import Invitation, Membership, Role

from .helpers import TenantTestBase, client_for, make_user


class InviteTests(TenantTestBase):
    def _invite(self, inviter, email, role):
        return client_for(inviter, self.host).post(
            "/api/team/invites/", {"email": email, "role": role}, format="json"
        )

    def _token_from_last_email(self):
        return re.search(r"Token: (\S+)", mail.outbox[-1].body).group(1)

    def test_invite_and_accept(self):
        self.assertEqual(self._invite(self.owner, "Invitee@Example.com", "viewer").status_code, 201)
        token = self._token_from_last_email()

        invitee = make_user("invitee@example.com")
        c = client_for(invitee, self.host)
        r = c.post("/api/team/invites/accept/", {"token": token}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Membership.objects.get(user=invitee, tenant=self.tenant).role, Role.VIEWER)

        # Token is single-use
        self.assertEqual(c.post("/api/team/invites/accept/", {"token": token}, format="json").status_code, 400)

    def test_token_only_works_for_the_invited_email(self):
        self._invite(self.owner, "right@example.com", "viewer")
        token = self._token_from_last_email()
        intruder = make_user("wrong@example.com")
        r = client_for(intruder, self.host).post("/api/team/invites/accept/", {"token": token}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Membership.objects.filter(user=intruder).exists())

    def test_expired_invite_rejected(self):
        self._invite(self.owner, "late@example.com", "viewer")
        token = self._token_from_last_email()
        Invitation.objects.update(expires_at=timezone.now() - timedelta(days=1))
        late = make_user("late@example.com")
        r = client_for(late, self.host).post("/api/team/invites/accept/", {"token": token}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_viewer_and_accountant_cannot_invite(self):
        for role in (Role.VIEWER, Role.ACCOUNTANT):
            user = self.add_member(f"{role.value}@acme.test", role)
            self.assertEqual(self._invite(user, "x@example.com", "viewer").status_code, 403)

    def test_only_owner_can_invite_admins(self):
        admin = self.add_member("admin@acme.test", Role.ADMIN)
        self.assertEqual(self._invite(admin, "a@example.com", "admin").status_code, 403)
        self.assertEqual(self._invite(admin, "b@example.com", "viewer").status_code, 201)
        self.assertEqual(self._invite(self.owner, "c@example.com", "admin").status_code, 201)

    def test_cannot_invite_owner_role(self):
        self.assertEqual(self._invite(self.owner, "boss@example.com", "owner").status_code, 400)


class MemberManagementTests(TenantTestBase):
    def _member_id(self, user):
        return Membership.objects.get(user=user, tenant=self.tenant).pk

    def test_owner_cannot_be_removed(self):
        admin = self.add_member("admin@acme.test", Role.ADMIN)
        url = f"/api/team/members/{self._member_id(self.owner)}/"
        self.assertEqual(client_for(admin, self.host).delete(url).status_code, 403)

    def test_admin_cannot_touch_other_admin(self):
        a1 = self.add_member("a1@acme.test", Role.ADMIN)
        a2 = self.add_member("a2@acme.test", Role.ADMIN)
        url = f"/api/team/members/{self._member_id(a2)}/"
        self.assertEqual(client_for(a1, self.host).patch(url, {"role": "viewer"}, format="json").status_code, 403)
        self.assertEqual(client_for(a1, self.host).delete(url).status_code, 403)

    def test_owner_can_change_role_and_remove(self):
        viewer = self.add_member("v@acme.test", Role.VIEWER)
        c = client_for(self.owner, self.host)
        url = f"/api/team/members/{self._member_id(viewer)}/"
        r = c.patch(url, {"role": "accountant"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["role"], "accountant")
        self.assertEqual(c.delete(url).status_code, 204)

    def test_cannot_promote_to_owner(self):
        viewer = self.add_member("v@acme.test", Role.VIEWER)
        url = f"/api/team/members/{self._member_id(viewer)}/"
        r = client_for(self.owner, self.host).patch(url, {"role": "owner"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_viewer_can_list_but_not_modify(self):
        viewer = self.add_member("v@acme.test", Role.VIEWER)
        c = client_for(viewer, self.host)
        self.assertEqual(c.get("/api/team/members/").status_code, 200)
        url = f"/api/team/members/{self._member_id(viewer)}/"
        self.assertEqual(c.patch(url, {"role": "admin"}, format="json").status_code, 403)

    def test_me_lists_memberships(self):
        r = client_for(self.owner).get("/api/auth/me/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["memberships"][0]["role"], "owner")
        self.assertEqual(r.data["memberships"][0]["domain"], "acme.localhost")
