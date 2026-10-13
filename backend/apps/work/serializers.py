from datetime import timedelta
from decimal import Decimal

from rest_framework import serializers

from apps.crm.models import Client
from apps.invoicing.serializers import _clean_currency
from apps.invoicing.services import get_profile

from . import services
from .hours import hours_label, value_minor
from .models import Expense, Project, TimeEntry


class ProjectSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    entry_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "name", "client", "client_name", "hourly_rate_minor", "currency", "billable", "status",
                  "notes", "entry_count", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
        extra_kwargs = {"hourly_rate_minor": {"min_value": 0, "max_value": 10**9}}

    def get_entry_count(self, obj):
        return obj.time_entries.count()

    def validate_currency(self, value):
        return _clean_currency(value)

    def validate(self, attrs):
        instance = self.instance
        client = attrs.get("client")
        if client is not None and (instance is None or client.pk != instance.client_id):
            services.require_active_client(client)
            if instance is not None and instance.time_entries.exists():
                raise serializers.ValidationError({"client": "This project already has time logged, so its client cannot change."})
        currency = attrs.get("currency")
        if instance is not None and currency and currency != instance.currency and instance.time_entries.exists():
            raise serializers.ValidationError({"currency": "This project already has time logged, so its currency cannot change."})
        return attrs


class TimeEntrySerializer(serializers.ModelSerializer):
    """How an entry is shown."""

    project_name = serializers.CharField(source="project.name", read_only=True)
    client = serializers.IntegerField(source="project.client_id", read_only=True)
    client_name = serializers.CharField(source="project.client.name", read_only=True)
    duration_seconds = serializers.SerializerMethodField()
    hours = serializers.SerializerMethodField()
    amount_minor = serializers.SerializerMethodField()
    is_running = serializers.BooleanField(read_only=True)
    billed = serializers.SerializerMethodField()

    class Meta:
        model = TimeEntry
        fields = ["id", "project", "project_name", "client", "client_name", "user_id", "user_email", "description",
                  "started_at", "ended_at", "duration_seconds", "hours", "billable", "hourly_rate_minor",
                  "amount_minor", "is_running", "invoice", "billed", "created_at"]
        read_only_fields = fields

    def get_duration_seconds(self, obj):
        return obj.current_seconds

    def get_hours(self, obj):
        return hours_label(obj.current_seconds)

    def get_amount_minor(self, obj):
        return value_minor(obj.current_seconds, obj.hourly_rate_minor)

    def get_billed(self, obj):
        return obj.invoice_id is not None


class TimeEntryCreateSerializer(serializers.Serializer):
    """A finished entry typed in by hand: a start, plus either an end or a length in minutes."""

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.select_related("client"))
    description = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(required=False)
    duration_minutes = serializers.IntegerField(required=False, min_value=1, max_value=1440)
    billable = serializers.BooleanField(required=False)
    hourly_rate_minor = serializers.IntegerField(required=False, min_value=0, max_value=10**9)

    def validate(self, attrs):
        has_end, has_minutes = "ended_at" in attrs, "duration_minutes" in attrs
        if has_end == has_minutes:
            raise serializers.ValidationError("Give either ended_at or duration_minutes (not both, not neither).")
        if has_minutes:
            attrs["ended_at"] = attrs["started_at"] + timedelta(minutes=attrs.pop("duration_minutes"))
        return attrs


class StartTimerSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.select_related("client"))
    description = serializers.CharField(required=False, allow_blank=True, max_length=300, default="")
    billable = serializers.BooleanField(required=False)


class TimeEntryUpdateSerializer(serializers.Serializer):
    description = serializers.CharField(required=False, allow_blank=True, max_length=300)
    billable = serializers.BooleanField(required=False)
    hourly_rate_minor = serializers.IntegerField(required=False, min_value=0, max_value=10**9)
    started_at = serializers.DateTimeField(required=False)
    ended_at = serializers.DateTimeField(required=False)


class ExpenseSerializer(serializers.ModelSerializer):
    ex_tax_minor = serializers.SerializerMethodField()
    project_name = serializers.CharField(source="project.name", read_only=True, default=None)
    client_name = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = ["id", "description", "vendor", "category", "amount_minor", "tax_minor", "ex_tax_minor", "currency",
                  "incurred_on", "project", "project_name", "client", "client_name", "billable", "invoice", "notes",
                  "created_by_email", "created_at"]
        read_only_fields = ["id", "invoice", "created_by_email", "created_at"]
        extra_kwargs = {"amount_minor": {"min_value": 1, "max_value": 10**12}, "tax_minor": {"min_value": 0}}

    def get_ex_tax_minor(self, obj):
        return obj.amount_minor - obj.tax_minor

    def get_client_name(self, obj):
        client = obj.client or (obj.project.client if obj.project_id else None)
        return client.name if client else None

    def validate_currency(self, value):
        return _clean_currency(value)

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        amount, tax = get("amount_minor"), get("tax_minor") or 0
        if amount is not None and tax > amount:
            raise serializers.ValidationError({"tax_minor": "The tax cannot be more than the total amount."})
        project, client = get("project"), get("client")
        if project and client and project.client_id != client.pk:
            raise serializers.ValidationError({"client": "This client does not own that project."})
        if get("billable") and not (project or client):
            raise serializers.ValidationError({"billable": "Pick a client or project to re-bill this expense to."})
        return attrs

    def create(self, validated_data):
        validated_data.setdefault("currency", get_profile().default_currency)
        validated_data["created_by_email"] = getattr(self.context["request"].user, "email", "") or ""
        return super().create(validated_data)


class WorkInvoiceSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    time_entry_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list, max_length=500)
    expense_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list, max_length=500)
    tax_rate_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    group_by = serializers.ChoiceField(choices=["project", "entry"], required=False, default="project")
    exchange_rate = serializers.DecimalField(max_digits=18, decimal_places=6, required=False, allow_null=True,
                                             default=None, min_value=Decimal("0.000001"))
