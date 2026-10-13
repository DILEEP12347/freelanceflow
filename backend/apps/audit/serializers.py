from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "actor_id", "actor_email", "action", "outcome", "status_code", "method", "path",
                  "object_type", "object_id", "parent_id", "object_repr", "changes", "ip_address", "user_agent"]
        read_only_fields = fields
