"""How much of each limit an organization is using. Imports are inside the function because this module
is loaded by shared-app code while the usage numbers come from tenant apps."""


def usage_snapshot(tenant, plan) -> dict:
    from apps.crm.usage import active_client_count
    from apps.invoicing.services import invoices_sent_this_month
    from apps.tenants.services import seats_in_use

    limits = plan.limits or {}
    return {
        "active_clients": {"used": active_client_count(), "limit": limits.get("max_active_clients")},
        "invoices_this_month": {"used": invoices_sent_this_month(), "limit": limits.get("max_invoices_per_month")},
        "team_members": {"used": seats_in_use(tenant), "limit": limits.get("max_team_members")},
    }
