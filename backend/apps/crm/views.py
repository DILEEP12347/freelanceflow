from rest_framework import viewsets

from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.order_by("-created_at")
    serializer_class = ClientSerializer
