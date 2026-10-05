from rest_framework import serializers

from .models import Invitation, Membership, Role


class MemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "email", "full_name", "role", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_role(self, value):
        if value == Role.OWNER:
            raise serializers.ValidationError("Ownership cannot be assigned here.")
        return value


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "invited_by", "created_at", "expires_at"]
        read_only_fields = fields

    def get_invited_by(self, obj):
        return obj.invited_by.email if obj.invited_by else None


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=[r for r in Role.values if r != Role.OWNER])

    def validate_email(self, value):
        value = value.strip().lower()
        tenant = self.context["tenant"]
        if Membership.objects.filter(tenant=tenant, user__email__iexact=value).exists():
            raise serializers.ValidationError("This person is already a member.")
        return value
