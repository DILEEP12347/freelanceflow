from django.contrib import admin

from .models import Plan, StripeEvent, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "price_minor", "stripe_price_id", "is_active", "sort_order")
    list_editable = ("stripe_price_id", "is_active", "sort_order")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "status", "current_period_end", "grace_until", "stripe_customer_id")
    list_filter = ("plan", "status")
    readonly_fields = ("last_event_ts", "created_at", "updated_at")


@admin.register(StripeEvent)
class StripeEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "type", "customer_id", "processed_at")
    list_filter = ("type",)
