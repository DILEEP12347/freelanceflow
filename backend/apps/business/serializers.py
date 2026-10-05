from rest_framework import serializers

from .models import BusinessProfile


class BusinessProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProfile
        fields = "__all__"
        read_only_fields = ["id", "updated_at"]

    def validate_default_currency(self, value):
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter currency code, e.g. INR, USD.")
        return value

    def validate_invoice_prefix(self, value):
        value = value.strip().upper()
        if not value.isalnum():
            raise serializers.ValidationError("Letters and digits only.")
        return value
