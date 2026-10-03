from django.db import models


class Client(models.Model):
    """Lives in EACH tenant's schema. No tenant FK needed: the schema is the isolation."""

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
