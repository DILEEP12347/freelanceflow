from rest_framework import serializers

from .models import Plan


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["code", "name", "description", "price_minor", "currency", "interval", "trial_days", "limits", "features"]
        read_only_fields = fields
