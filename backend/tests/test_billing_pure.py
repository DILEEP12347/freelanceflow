"""No database: webhook signature checking and Stripe payload parsing."""
import hashlib
import hmac
import time

from django.test import SimpleTestCase

from apps.billing import signatures, stripe_payloads as sp

SECRET, BODY = "whsec_test", b'{"id":"evt_1"}'


def header(body=BODY, secret=SECRET, ts=None):
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


class SignatureTests(SimpleTestCase):
    def test_valid_signature_passes(self):
        signatures.verify_signature(BODY, header(), SECRET)

    def test_any_of_several_v1_signatures_may_match(self):  # Stripe sends two while a secret is being rotated
        good = header()
        signatures.verify_signature(BODY, good.replace(",v1=", ",v1=deadbeef,v1=", 1), SECRET)

    def test_bad_inputs_are_rejected(self):
        ts = int(time.time())
        for bad in ("", "garbage", "t=abc,v1=00", f"v1={header().split('v1=')[1]}", f"t={ts},v1=deadbeef",
                    header(ts=ts - 1000)):
            with self.assertRaises(signatures.SignatureError, msg=bad):
                signatures.verify_signature(BODY, bad, SECRET)

    def test_tampered_body_or_wrong_secret_or_empty_secret(self):
        with self.assertRaises(signatures.SignatureError):
            signatures.verify_signature(BODY + b" ", header(), SECRET)
        with self.assertRaises(signatures.SignatureError):
            signatures.verify_signature(BODY, header(secret="whsec_other"), SECRET)
        with self.assertRaises(signatures.SignatureError):
            signatures.verify_signature(BODY, header(), "")


class PayloadTests(SimpleTestCase):
    old = {"id": "sub_1", "current_period_end": 1790000000, "items": {"data": [{"price": {"id": "price_pro"}}]}}
    new = {"id": "sub_1", "items": {"data": [{"price": {"id": "price_biz"}, "current_period_end": 1790000000}]}}

    def test_period_end_is_read_from_either_api_shape(self):
        self.assertEqual(sp.extract_period_end(self.old), sp.extract_period_end(self.new))
        self.assertEqual(sp.extract_period_end(self.old).year, 2026)
        self.assertIsNone(sp.extract_period_end({}))

    def test_price_and_ids(self):
        self.assertEqual(sp.extract_price_id(self.old), "price_pro")
        self.assertEqual(sp.extract_price_id(self.new), "price_biz")
        self.assertEqual(sp.extract_price_id({}), "")
        self.assertEqual(sp.id_of({"id": "cus_2"}), "cus_2")
        self.assertEqual(sp.id_of("cus_1"), "cus_1")
        self.assertEqual(sp.id_of(None), "")

    def test_tenant_id(self):
        self.assertEqual(sp.extract_tenant_id({"metadata": {"tenant_id": "7"}}), 7)
        self.assertEqual(sp.extract_tenant_id({"client_reference_id": "9"}), 9)
        self.assertIsNone(sp.extract_tenant_id({"metadata": {"tenant_id": "abc"}}))
        self.assertIsNone(sp.extract_tenant_id({}))
