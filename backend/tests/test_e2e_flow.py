from .test_week7_work import Week7Base


class EndToEndFlowTests(Week7Base):
    """One walk through the whole product: client -> time -> invoice -> payment."""

    def test_full_freelancer_flow(self):
        # 1. client and project
        client = self.make_client("E2E Co", payment_terms_days=30)
        project = self.make_project(client_id=client["id"])

        # 2. billable work: one time entry and one expense
        entry = self.add_time(project["id"], minutes=60)
        expense = self.make_expense(client=client["id"], billable=True)

        # 3. bill both into one invoice
        r = self.work_invoice(client["id"], [entry["id"]], [expense["id"]])
        self.assertIn(r.status_code, (200, 201), r.data)
        self.assertEqual(r.data["total_minor"], 268000, r.data)
        invoice_id = r.data["invoice_id"]

        # 4. the same time entry cannot be billed twice
        again = self.work_invoice(client["id"], [entry["id"]])
        self.assertGreaterEqual(again.status_code, 400, again.data)

        # 5. send it
        r = self.send(invoice_id)
        self.assertIn(r.status_code, (200, 201), r.data)

        # 6. record a part payment
        r = self.pay(invoice_id, 10000)
        self.assertIn(r.status_code, (200, 201), r.data)

        # 7. the invoice is now partially paid
        r = self.api.get(f"/api/invoices/{invoice_id}/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "partial", r.data)