from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    CLOSED_STAGES,
    Activity,
    ActivityKind,
    Client,
    ClientStatus,
    Contact,
    LeadStage,
)


def actor_fields(user):
    return {"actor_id": getattr(user, "pk", None), "actor_email": getattr(user, "email", "") or ""}


def log_activity(kind, message, actor=None, client=None, lead=None, metadata=None):
    return Activity.objects.create(
        kind=kind, message=message, client=client, lead=lead, metadata=metadata or {}, **actor_fields(actor)
    )


def stage_label(value):
    return dict(LeadStage.choices).get(value, value)


def archive_client(client, actor):
    if client.status == ClientStatus.ARCHIVED:
        return client
    client.status = ClientStatus.ARCHIVED
    client.archived_at = timezone.now()
    client.save(update_fields=["status", "archived_at", "updated_at"])
    log_activity(ActivityKind.CLIENT_ARCHIVED, f"Archived {client.name}", actor, client=client)
    return client


def restore_client(client, actor):
    # Week 5: an archived client coming back counts toward the plan's active-client limit,
    # so the limit check goes here.
    if client.status == ClientStatus.ACTIVE:
        return client
    client.status = ClientStatus.ACTIVE
    client.archived_at = None
    client.save(update_fields=["status", "archived_at", "updated_at"])
    log_activity(ActivityKind.CLIENT_RESTORED, f"Restored {client.name}", actor, client=client)
    return client


def finalize_stage_change(lead, old_stage, actor):
    """Call AFTER lead.stage has been changed and saved: stamps closed_at and logs the move."""
    if lead.stage in CLOSED_STAGES:
        lead.closed_at = lead.closed_at or timezone.now()
    else:
        lead.closed_at = None
    lead.save(update_fields=["closed_at", "updated_at"])
    log_activity(
        ActivityKind.STAGE_CHANGED,
        f"Stage changed from {stage_label(old_stage)} to {stage_label(lead.stage)}",
        actor,
        client=lead.client,
        lead=lead,
        metadata={"from": old_stage, "to": lead.stage},
    )


def move_lead(lead, new_stage, actor):
    old = lead.stage
    if new_stage == old:
        return lead
    lead.stage = new_stage
    lead.save(update_fields=["stage", "updated_at"])
    finalize_stage_change(lead, old, actor)
    return lead


def convert_lead(lead, actor, client=None):
    """Turn a lead into a client (or link it to an existing one) and mark it won."""
    if lead.client_id:
        raise ValidationError({"detail": "This lead is already linked to a client."})
    with transaction.atomic():
        if client is None:
            # Week 5: creating an active client counts toward the plan limit, so check it here.
            client = Client.objects.create(
                name=lead.company_name or lead.contact_name or lead.title,
                company_name=lead.company_name,
                email=lead.contact_email,
                phone=lead.contact_phone,
            )
            log_activity(
                ActivityKind.CLIENT_CREATED, f"Client created from lead: {lead.title}", actor,
                client=client, lead=lead,
            )
            if lead.company_name and lead.contact_name:
                Contact.objects.create(
                    client=client, name=lead.contact_name, email=lead.contact_email,
                    phone=lead.contact_phone, is_primary=True,
                )
        lead.client = client
        lead.save(update_fields=["client", "updated_at"])
        log_activity(
            ActivityKind.LEAD_CONVERTED, f"Lead converted: {lead.title}", actor, client=client, lead=lead
        )
        move_lead(lead, LeadStage.WON, actor)
    return client
