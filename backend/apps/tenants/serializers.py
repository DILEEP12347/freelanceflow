from rest_framework import serializers


class SignupSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    subdomain = serializers.CharField(max_length=30)
