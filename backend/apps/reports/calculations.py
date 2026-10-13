"""The report queries. Every figure is per currency (INR is never added to USD) and in integer minor units."""
from collections import defaultdict

from django.db.models import BigIntegerField, Count, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncMonth

from apps.invoicing.models import Invoice, InvoiceStatus, Payment
from apps.work.hours import value_from_product
from apps.work.models import Expense, TimeEntry

from .periods import BUCKETS, bucket_for, month_key, month_keys

BILLED = (InvoiceStatus.SENT, InvoiceStatus.PARTIAL, InvoiceStatus.PAID)  # drafts and voids are not revenue
OPEN = (InvoiceStatus.SENT, InvoiceStatus.PARTIAL)


def _sorted_currencies(*dicts):
    return sorted(set().union(*[d.keys() for d in dicts]))


def revenue_report(start, end):
    """Invoiced (by issue date) and collected (by payment date) per month."""
    invoiced = defaultdict(dict)
    for r in (Invoice.objects.filter(status__in=BILLED, issue_date__range=(start, end))
              .annotate(m=TruncMonth("issue_date")).values("currency", "m").order_by()
              .annotate(total=Sum("total_minor"), tax=Sum("tax_minor"), n=Count("id"))):
        invoiced[r["currency"]][month_key(r["m"])] = r
    collected = defaultdict(dict)
    for r in (Payment.objects.filter(paid_on__range=(start, end))
              .annotate(m=TruncMonth("paid_on")).values("invoice__currency", "m").order_by()
              .annotate(total=Sum("amount_minor"))):
        collected[r["invoice__currency"]][month_key(r["m"])] = r["total"]

    out = []
    for currency in _sorted_currencies(invoiced, collected):
        months = []
        for key in month_keys(start, end):
            inv = invoiced[currency].get(key)
            months.append({
                "month": key, "invoiced_minor": inv["total"] if inv else 0, "tax_minor": inv["tax"] if inv else 0,
                "invoice_count": inv["n"] if inv else 0, "collected_minor": collected[currency].get(key, 0),
            })
        totals = {k: sum(m[k] for m in months) for k in ("invoiced_minor", "tax_minor", "invoice_count", "collected_minor")}
        out.append({"currency": currency, "totals": totals, "months": months})
    return {"from": start, "to": end, "currencies": out}


def revenue_table(report):
    rows = [[c["currency"], m["month"], m["invoiced_minor"], m["tax_minor"], m["invoice_count"], m["collected_minor"]]
            for c in report["currencies"] for m in c["months"]]
    return ["currency", "month", "invoiced_minor", "tax_minor", "invoice_count", "collected_minor"], rows


def receivables_report(as_of):
    """Money still owed, by how late it is."""
    totals = defaultdict(lambda: dict.fromkeys(BUCKETS, 0))
    clients = defaultdict(dict)
    for r in (Invoice.objects.filter(status__in=OPEN)
              .values("currency", "client_id", "client__name", "total_minor", "amount_paid_minor", "due_date").order_by()):
        balance = r["total_minor"] - r["amount_paid_minor"]
        late = (as_of - r["due_date"]).days if r["due_date"] else 0
        bucket = bucket_for(late)
        totals[r["currency"]][bucket] += balance
        row = clients[r["currency"]].setdefault(
            r["client_id"], {"client_id": r["client_id"], "client_name": r["client__name"], **dict.fromkeys(BUCKETS, 0), "total_minor": 0})
        row[bucket] += balance
        row["total_minor"] += balance

    out = []
    for currency in sorted(totals):
        out.append({
            "currency": currency, "buckets": dict(totals[currency]), "total_minor": sum(totals[currency].values()),
            "clients": sorted(clients[currency].values(), key=lambda c: -c["total_minor"]),
        })
    return {"as_of": as_of, "currencies": out}


def receivables_table(report):
    rows = [[c["currency"], cl["client_name"], *[cl[b] for b in BUCKETS], cl["total_minor"]]
            for c in report["currencies"] for cl in c["clients"]]
    return ["currency", "client", *BUCKETS, "total_minor"], rows


def profit_loss_report(start, end):
    """Accrual basis, tax excluded: revenue = invoice subtotals by issue date, costs = expenses without their GST.
    The GST collected and the GST paid on expenses are shown separately, with the net amount owed."""
    income = defaultdict(dict)
    for r in (Invoice.objects.filter(status__in=BILLED, issue_date__range=(start, end))
              .annotate(m=TruncMonth("issue_date")).values("currency", "m").order_by()
              .annotate(sub=Sum("subtotal_minor"), tax=Sum("tax_minor"))):
        income[r["currency"]][month_key(r["m"])] = r
    spend = defaultdict(dict)
    for r in (Expense.objects.filter(incurred_on__range=(start, end))
              .annotate(m=TruncMonth("incurred_on")).values("currency", "m").order_by()
              .annotate(amount=Sum("amount_minor"), tax=Sum("tax_minor"))):
        spend[r["currency"]][month_key(r["m"])] = r
    categories = defaultdict(list)
    for r in (Expense.objects.filter(incurred_on__range=(start, end)).values("currency", "category").order_by()
              .annotate(amount=Sum("amount_minor"), tax=Sum("tax_minor"))):
        categories[r["currency"]].append({"category": r["category"], "amount_minor": r["amount"] - r["tax"]})

    out = []
    for currency in _sorted_currencies(income, spend):
        months, gst_in, gst_out = [], 0, 0
        for key in month_keys(start, end):
            i, s = income[currency].get(key), spend[currency].get(key)
            revenue = i["sub"] if i else 0
            costs = (s["amount"] - s["tax"]) if s else 0
            gst_out += i["tax"] if i else 0
            gst_in += s["tax"] if s else 0
            months.append({"month": key, "income_minor": revenue, "expenses_minor": costs, "profit_minor": revenue - costs})
        revenue = sum(m["income_minor"] for m in months)
        costs = sum(m["expenses_minor"] for m in months)
        out.append({
            "currency": currency,
            "totals": {"income_minor": revenue, "expenses_minor": costs, "profit_minor": revenue - costs,
                       "gst_collected_minor": gst_out, "gst_paid_minor": gst_in, "net_gst_payable_minor": gst_out - gst_in},
            "months": months,
            "expenses_by_category": sorted(categories[currency], key=lambda c: -c["amount_minor"]),
        })
    return {"from": start, "to": end, "basis": "accrual, excluding tax", "currencies": out}


def profit_loss_table(report):
    rows = [[c["currency"], m["month"], m["income_minor"], m["expenses_minor"], m["profit_minor"]]
            for c in report["currencies"] for m in c["months"]]
    return ["currency", "month", "income_minor", "expenses_minor", "profit_minor"], rows


def clients_report(start, end, limit=20):
    """Who brings in the money: invoiced and collected in the period, plus what each client owes right now."""
    invoiced = defaultdict(dict)
    for r in (Invoice.objects.filter(status__in=BILLED, issue_date__range=(start, end))
              .values("currency", "client_id", "client__name").order_by().annotate(total=Sum("total_minor"), n=Count("id"))):
        invoiced[r["currency"]][r["client_id"]] = r
    collected = defaultdict(dict)
    for r in (Payment.objects.filter(paid_on__range=(start, end)).values("invoice__currency", "invoice__client_id")
              .order_by().annotate(total=Sum("amount_minor"))):
        collected[r["invoice__currency"]][r["invoice__client_id"]] = r["total"]
    owed = defaultdict(lambda: defaultdict(int))
    for r in Invoice.objects.filter(status__in=OPEN).values("currency", "client_id", "total_minor", "amount_paid_minor").order_by():
        owed[r["currency"]][r["client_id"]] += r["total_minor"] - r["amount_paid_minor"]

    out = []
    for currency in _sorted_currencies(invoiced, collected, owed):
        rows = []
        ids = set(invoiced[currency]) | set(collected[currency]) | set(owed[currency])
        names = {i: invoiced[currency][i]["client__name"] for i in invoiced[currency]}
        missing = [i for i in ids if i not in names]
        if missing:
            from apps.crm.models import Client

            names.update(dict(Client.objects.filter(pk__in=missing).values_list("pk", "name")))
        for cid in ids:
            inv = invoiced[currency].get(cid)
            rows.append({"client_id": cid, "client_name": names.get(cid, ""), "invoiced_minor": inv["total"] if inv else 0,
                         "invoice_count": inv["n"] if inv else 0, "collected_minor": collected[currency].get(cid, 0),
                         "outstanding_minor": owed[currency].get(cid, 0)})
        rows.sort(key=lambda r: (-r["invoiced_minor"], r["client_name"]))
        out.append({"currency": currency, "clients": rows[:limit]})
    return {"from": start, "to": end, "currencies": out}


def clients_table(report):
    rows = [[c["currency"], cl["client_name"], cl["invoiced_minor"], cl["invoice_count"], cl["collected_minor"], cl["outstanding_minor"]]
            for c in report["currencies"] for cl in c["clients"]]
    return ["currency", "client", "invoiced_minor", "invoice_count", "collected_minor", "outstanding_minor"], rows


def time_report(start_dt, end_dt):
    """Hours by project and by person, and the value of work not yet invoiced."""
    qs = TimeEntry.objects.filter(ended_at__isnull=False)
    if start_dt:
        qs = qs.filter(started_at__gte=start_dt)
    if end_dt:
        qs = qs.filter(started_at__lt=end_dt)
    worth = ExpressionWrapper(F("duration_seconds") * F("hourly_rate_minor"), output_field=BigIntegerField())
    unbilled = Q(billable=True, invoice__isnull=True)
    projects = []
    for r in (qs.values("project_id", "project__name", "project__client__name", "project__currency").order_by()
              .annotate(seconds=Sum("duration_seconds"), billable_secs=Sum("duration_seconds", filter=Q(billable=True)),
                        unbilled=Sum("duration_seconds", filter=unbilled), billed=Sum("duration_seconds", filter=Q(invoice__isnull=False)),
                        unbilled_worth=Sum(worth, filter=unbilled))):
        projects.append({
            "project_id": r["project_id"], "project": r["project__name"], "client": r["project__client__name"],
            "currency": r["project__currency"], "total_seconds": r["seconds"] or 0, "billable_seconds": r["billable_secs"] or 0,
            "billed_seconds": r["billed"] or 0, "unbilled_seconds": r["unbilled"] or 0,
            "unbilled_value_minor": value_from_product(r["unbilled_worth"] or 0),
        })
    projects.sort(key=lambda p: -p["total_seconds"])
    people = [{"user_email": r["user_email"], "total_seconds": r["seconds"] or 0, "billable_seconds": r["billable_secs"] or 0}
              for r in qs.values("user_email").order_by().annotate(seconds=Sum("duration_seconds"), billable_secs=Sum("duration_seconds", filter=Q(billable=True)))]
    people.sort(key=lambda p: -p["total_seconds"])
    unbilled_by_currency = defaultdict(int)
    for p in projects:
        unbilled_by_currency[p["currency"]] += p["unbilled_value_minor"]
    return {"projects": projects, "people": people,
            "unbilled_value_by_currency": [{"currency": c, "amount_minor": v} for c, v in sorted(unbilled_by_currency.items())]}
