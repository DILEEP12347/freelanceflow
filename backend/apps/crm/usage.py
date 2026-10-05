"""Usage numbers for the CRM. Week 5 compares these against the tenant's plan limits
(e.g. Free = 5 active clients) before allowing new clients."""
from django.db.models import Count, Q, Sum

from .models import OPEN_STAGES, Client, ClientStatus, Lead, LeadStage


def active_client_count() -> int:
    return Client.objects.filter(status=ClientStatus.ACTIVE).count()


def usage_snapshot() -> dict:
    clients = Client.objects.aggregate(
        active=Count("id", filter=Q(status=ClientStatus.ACTIVE)),
        archived=Count("id", filter=Q(status=ClientStatus.ARCHIVED)),
    )
    by_stage = {
        row["stage"]: row
        for row in Lead.objects.values("stage").annotate(n=Count("id"), total=Sum("value_minor"))
    }

    def n(stage):
        return by_stage.get(stage, {}).get("n", 0)

    def total(stage):
        return by_stage.get(stage, {}).get("total") or 0

    won, lost = n(LeadStage.WON), n(LeadStage.LOST)
    return {
        "active_clients": clients["active"],
        "archived_clients": clients["archived"],
        "open_leads": sum(n(s) for s in OPEN_STAGES),
        "open_pipeline_value_minor": sum(total(s) for s in OPEN_STAGES),
        "won_leads": won,
        "won_value_minor": total(LeadStage.WON),
        "lost_leads": lost,
        "conversion_rate": round(won / (won + lost), 4) if (won + lost) else None,
        "leads_by_stage": {value: n(value) for value in LeadStage.values},
    }
