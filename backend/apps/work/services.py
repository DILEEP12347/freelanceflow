"""All time-tracking and expense rules. Views stay thin; the invoice builder reuses the invoicing services."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.crm.models import ClientStatus
from apps.invoicing import services as invoicing

from .hours import MAX_ENTRY_SECONDS, hours_label, hours_quantity
from .models import Expense, Project, ProjectStatus, TimeEntry

FUTURE_SLACK = timedelta(minutes=5)  # tolerate a slightly fast clock, nothing more


# ---------------- projects ----------------
def require_active_client(client):
    if client.status == ClientStatus.ARCHIVED:
        raise ValidationError({"client": "This client is archived. Restore the client first."})


def archive_project(project):
    project.status = ProjectStatus.ARCHIVED
    project.save(update_fields=["status"])
    return project


def restore_project(project):
    require_active_client(project.client)
    project.status = ProjectStatus.ACTIVE
    project.save(update_fields=["status"])
    return project


def _require_active_project(project):
    if project.status != ProjectStatus.ACTIVE:
        raise ValidationError({"project": "This project is archived. Restore it to log time."})


# ---------------- the timer ----------------
def _finish(entry, ended_at):
    ended_at = max(ended_at, entry.started_at)
    entry.ended_at = ended_at
    entry.duration_seconds = min(int((ended_at - entry.started_at).total_seconds()), MAX_ENTRY_SECONDS)
    entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])


@transaction.atomic
def start_timer(user, project, description="", billable=None, now=None):
    """Start a timer. A person can only work on one thing at a time, so any timer they still have
    running is stopped first. -> (new_entry, previously_running_entry_or_None)"""
    _require_active_project(project)
    now = now or timezone.now()
    stopped = None
    for running in TimeEntry.objects.select_for_update().filter(user_id=user.pk, ended_at__isnull=True):
        _finish(running, now)
        stopped = running
    entry = TimeEntry.objects.create(
        project=project, user_id=user.pk, user_email=getattr(user, "email", "") or "", description=description,
        started_at=now, billable=project.billable if billable is None else billable,
        hourly_rate_minor=project.hourly_rate_minor,
    )
    return entry, stopped


@transaction.atomic
def stop_timer(entry, now=None):
    entry = TimeEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.ended_at is not None:
        raise ValidationError({"detail": "This timer is already stopped."})
    _finish(entry, now or timezone.now())
    return entry


# ---------------- manual entries and edits ----------------
def check_range(started_at, ended_at):
    if ended_at <= started_at:
        raise ValidationError({"ended_at": "The end must be after the start."})
    if (ended_at - started_at).total_seconds() > MAX_ENTRY_SECONDS:
        raise ValidationError({"ended_at": "One entry cannot be longer than 24 hours. Split it into two."})
    if ended_at > timezone.now() + FUTURE_SLACK:
        raise ValidationError({"ended_at": "That time is in the future."})


def create_manual_entry(user, project, started_at, ended_at, description="", billable=None, hourly_rate_minor=None):
    _require_active_project(project)
    check_range(started_at, ended_at)
    return TimeEntry.objects.create(
        project=project, user_id=user.pk, user_email=getattr(user, "email", "") or "", description=description,
        started_at=started_at, ended_at=ended_at, duration_seconds=int((ended_at - started_at).total_seconds()),
        billable=project.billable if billable is None else billable,
        hourly_rate_minor=project.hourly_rate_minor if hourly_rate_minor is None else hourly_rate_minor,
    )


@transaction.atomic
def update_entry(entry, **fields):
    entry = TimeEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.invoice_id:
        raise ValidationError({"detail": "This entry is already on an invoice and cannot be changed."})
    timing = {"started_at", "ended_at"} & set(fields)
    if entry.ended_at is None and timing:
        raise ValidationError({"detail": "Stop the timer before changing its times."})
    for name, value in fields.items():
        setattr(entry, name, value)
    if timing:
        check_range(entry.started_at, entry.ended_at)
        entry.duration_seconds = int((entry.ended_at - entry.started_at).total_seconds())
    entry.save()
    return entry


def delete_entry(entry):
    if entry.invoice_id:
        raise ValidationError({"detail": "This entry is already on an invoice and cannot be deleted."})
    entry.delete()


# ---------------- turning work into an invoice ----------------
def _client_of_expense(expense):
    return expense.client_id or (expense.project.client_id if expense.project_id else None)


@transaction.atomic
def invoice_from_work(actor, client, time_ids, expense_ids, tax_rate=None, group_by="project", exchange_rate=None):
    """Create ONE draft invoice from unbilled time and billable expenses, and mark them as billed.
    -> (invoice, number_of_time_entries, number_of_expenses)

    Everything is validated before anything is created, and every problem is reported at once."""
    time_ids, expense_ids = list(dict.fromkeys(time_ids)), list(dict.fromkeys(expense_ids))
    if not time_ids and not expense_ids:
        raise ValidationError({"detail": "Choose at least one time entry or expense."})
    entries = list(TimeEntry.objects.select_for_update(of=("self",)).select_related("project").filter(pk__in=time_ids))
    expenses = list(Expense.objects.select_for_update(of=("self",)).select_related("project").filter(pk__in=expense_ids))

    problems = []
    if len(entries) != len(time_ids):
        problems.append("Some time entries were not found.")
    if len(expenses) != len(expense_ids):
        problems.append("Some expenses were not found.")
    for e in entries:
        label = f"Time entry {e.pk}"
        if e.ended_at is None:
            problems.append(f"{label} is still running. Stop the timer first.")
        if not e.billable:
            problems.append(f"{label} is not billable.")
        if e.invoice_id:
            problems.append(f"{label} is already on an invoice.")
        if e.project.client_id != client.pk:
            problems.append(f"{label} belongs to a different client.")
    for x in expenses:
        label = f"Expense {x.pk}"
        if not x.billable:
            problems.append(f"{label} is not marked billable.")
        if x.invoice_id:
            problems.append(f"{label} is already on an invoice.")
        if _client_of_expense(x) != client.pk:
            problems.append(f"{label} does not belong to this client.")
    currencies = {e.project.currency for e in entries} | {x.currency for x in expenses}
    if len(currencies) > 1:
        problems.append("Items are in different currencies. Invoice them separately.")
    if problems:
        raise ValidationError({"items": problems})

    tax_name, tax_bps = (tax_rate.name, tax_rate.rate_bps) if tax_rate else ("", 0)
    lines = []
    if group_by == "entry":
        for e in sorted(entries, key=lambda e: e.started_at):
            what = e.description or "Time"
            lines.append({
                "description": f"{e.project.name}: {what} ({e.started_at.date().isoformat()})"[:500],
                "quantity": hours_quantity(e.duration_seconds), "unit_price_minor": e.hourly_rate_minor,
                "tax_name": tax_name, "tax_rate_bps": tax_bps,
            })
    else:  # one line per project and rate
        groups = {}
        for e in entries:
            groups.setdefault((e.project_id, e.hourly_rate_minor), []).append(e)
        for (_, rate), items in sorted(groups.items(), key=lambda kv: kv[1][0].project.name):
            seconds = sum(i.duration_seconds for i in items)
            noun = "entry" if len(items) == 1 else "entries"
            lines.append({
                "description": f"{items[0].project.name} - {hours_label(seconds)} h ({len(items)} {noun})"[:500],
                "quantity": hours_quantity(seconds), "unit_price_minor": rate,
                "tax_name": tax_name, "tax_rate_bps": tax_bps,
            })
    for x in sorted(expenses, key=lambda x: (x.incurred_on, x.pk)):
        vendor = f" ({x.vendor})" if x.vendor else ""
        lines.append({
            "description": f"Expense: {x.description}{vendor}"[:500], "quantity": 1,
            "unit_price_minor": x.amount_minor, "tax_name": "", "tax_rate_bps": 0,  # passed through at cost
        })

    invoice = invoicing.create_invoice(
        actor, client, lines=lines, currency=currencies.pop(), exchange_rate=exchange_rate
    )
    TimeEntry.objects.filter(pk__in=[e.pk for e in entries]).update(invoice=invoice)
    Expense.objects.filter(pk__in=[x.pk for x in expenses]).update(invoice=invoice)
    return invoice, len(entries), len(expenses)


def release_work(invoice):
    """A voided invoice gives its time and expenses back, so they can be billed again."""
    TimeEntry.objects.filter(invoice=invoice).update(invoice=None)
    Expense.objects.filter(invoice=invoice).update(invoice=None)
