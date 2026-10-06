from decimal import Decimal

from rest_framework import serializers

from apps.crm.models import Client

from . import services
from .calculations import tax_summary
from .models import Invoice, InvoiceLine, Payment, TaxRate


def _clean_currency(value):
    value = value.strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise serializers.ValidationError("Use a 3-letter currency code, e.g. INR, USD.")
    return value


class TaxRateSerializer(serializers.ModelSerializer):
    rate_percent = serializers.SerializerMethodField()

    class Meta:
        model = TaxRate
        fields = ["id", "name", "rate_bps", "rate_percent", "is_default", "is_active"]
        read_only_fields = ["id"]
        extra_kwargs = {"rate_bps": {"max_value": 10000}}

    def get_rate_percent(self, obj):
        return str((Decimal(obj.rate_bps) / 100).quantize(Decimal("0.01")))


class InvoiceLineSerializer(serializers.ModelSerializer):
    """How a line is shown."""

    class Meta:
        model = InvoiceLine
        fields = [
            "id", "position", "description", "quantity", "unit_price_minor", "tax_name", "tax_rate_bps",
            "subtotal_minor", "tax_minor", "total_minor",
        ]
        read_only_fields = fields


class InvoiceLineInputSerializer(serializers.Serializer):
    """How a line is sent. Amounts are always computed by the server, never trusted from the client."""

    description = serializers.CharField(max_length=500)
    quantity = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), default=Decimal("1"))
    unit_price_minor = serializers.IntegerField(min_value=0, max_value=10**12)
    tax_rate_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_description(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A line needs a description.")
        return value

    def validate(self, attrs):
        rate_id = attrs.pop("tax_rate_id", None)
        if rate_id is None:
            attrs["tax_name"], attrs["tax_rate_bps"] = "", 0
        else:
            rate = TaxRate.objects.filter(pk=rate_id, is_active=True).first()
            if rate is None:
                raise serializers.ValidationError({"tax_rate_id": "Unknown or inactive tax rate."})
            attrs["tax_name"], attrs["tax_rate_bps"] = rate.name, rate.rate_bps
        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "amount_minor", "paid_on", "method", "reference", "notes", "recorded_by_email", "created_at"]
        read_only_fields = ["id", "recorded_by_email", "created_at"]
        extra_kwargs = {"amount_minor": {"min_value": 1}, "paid_on": {"required": False}}


class InvoiceListSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    display_status = serializers.CharField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    balance_due_minor = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "status", "display_status", "is_overdue", "client", "client_name",
            "issue_date", "due_date", "currency", "total_minor", "amount_paid_minor",
            "balance_due_minor", "created_at",
        ]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    """Detail view and write serializer. Send `lines` as a list; it REPLACES the draft's lines."""

    client_name = serializers.CharField(source="client.name", read_only=True)
    display_status = serializers.CharField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    balance_due_minor = serializers.IntegerField(read_only=True)
    lines = InvoiceLineInputSerializer(many=True, write_only=True, required=False)
    payments = PaymentSerializer(many=True, read_only=True)
    tax_summary = serializers.SerializerMethodField()
    gst_mode = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id", "number", "status", "display_status", "is_overdue", "client", "client_name",
            "issue_date", "due_date", "currency", "exchange_rate",
            "subtotal_minor", "tax_minor", "total_minor", "amount_paid_minor", "balance_due_minor",
            "notes", "terms", "void_reason", "snapshot", "tax_summary", "gst_mode", "lines", "payments",
            "created_by_email", "created_at", "updated_at", "sent_at", "paid_at", "voided_at",
        ]
        read_only_fields = [
            "id", "number", "status", "subtotal_minor", "tax_minor", "total_minor", "amount_paid_minor",
            "void_reason", "snapshot", "created_by_email", "created_at", "updated_at", "sent_at",
            "paid_at", "voided_at",
        ]

    def get_tax_summary(self, obj):
        return tax_summary(list(obj.lines.all()))

    def get_gst_mode(self, obj):
        return (obj.snapshot or {}).get("gst_mode")

    def validate_currency(self, value):
        return _clean_currency(value)

    def validate_exchange_rate(self, value):
        if value <= 0:
            raise serializers.ValidationError("The exchange rate must be greater than zero.")
        return value

    def validate(self, attrs):
        issue = attrs.get("issue_date", getattr(self.instance, "issue_date", None))
        due = attrs.get("due_date", getattr(self.instance, "due_date", None))
        if issue and due and due < issue:
            raise serializers.ValidationError({"due_date": "The due date cannot be before the issue date."})
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["lines"] = InvoiceLineSerializer(instance.lines.all(), many=True).data
        return data

    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        return services.create_invoice(self.context["request"].user, lines=lines, **validated_data)

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        return services.update_draft(instance, self.context["request"].user, lines=lines, **validated_data)


class SendInvoiceSerializer(serializers.Serializer):
    issue_date = serializers.DateField(required=False)
    due_date = serializers.DateField(required=False)


class VoidInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)
