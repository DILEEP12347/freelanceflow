import re

from rest_framework import serializers

from .models import Activity, Client, Contact, Lead, Note, Tag

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "color"]

    def validate_name(self, value):
        value = value.strip()
        clash = Tag.objects.filter(name__iexact=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A tag with this name already exists.")
        return value

    def validate_color(self, value):
        if not HEX_COLOR.match(value):
            raise serializers.ValidationError("Use a hex color like #1d4ed8.")
        return value.lower()


class ClientSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    tag_ids = serializers.PrimaryKeyRelatedField(
        source="tags", queryset=Tag.objects.all(), many=True, write_only=True, required=False
    )

    class Meta:
        model = Client
        fields = [
            "id", "name", "company_name", "email", "phone", "website", "tax_id",
            "address_line1", "address_line2", "city", "state", "postal_code", "country",
            "payment_terms_days", "status", "tags", "tag_ids",
            "created_at", "updated_at", "archived_at",
        ]
        # status changes only through /archive/ and /restore/, so every change is logged.
        read_only_fields = ["id", "status", "archived_at", "created_at", "updated_at"]
        extra_kwargs = {"payment_terms_days": {"max_value": 365}}


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "name", "email", "phone", "job_title", "is_primary", "created_at"]
        read_only_fields = ["id", "created_at"]


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "body", "author_email", "created_at"]
        read_only_fields = ["id", "author_email", "created_at"]

    def validate_body(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A note cannot be empty.")
        return value


class LeadSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()

    class Meta:
        model = Lead
        fields = [
            "id", "title", "contact_name", "contact_email", "contact_phone", "company_name",
            "client", "client_name", "stage", "value_minor", "currency",
            "expected_close_date", "source", "notes", "created_at", "updated_at", "closed_at",
        ]
        read_only_fields = ["id", "client", "created_at", "updated_at", "closed_at"]
        extra_kwargs = {"value_minor": {"min_value": 0}}

    def get_client_name(self, obj):
        return obj.client.name if obj.client_id else None

    def validate_currency(self, value):
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter currency code, e.g. INR, USD.")
        return value


class LeadMoveSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Lead._meta.get_field("stage").choices)


class LeadConvertSerializer(serializers.Serializer):
    client_id = serializers.IntegerField(required=False)


class ActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Activity
        fields = ["id", "kind", "message", "metadata", "actor_email", "client", "lead", "created_at"]
        read_only_fields = fields
