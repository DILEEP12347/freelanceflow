from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """Who did what, and when. Append-only: entries are never edited, and there is no API to change or delete them.

    It records the REQUEST (what was asked, by whom, whether it was allowed), with passwords and tokens removed.
    It is not a field-by-field before/after diff of the database."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)
    action = models.CharField(max_length=80, db_index=True)       # "invoice.voided", "member.role_changed", ...
    outcome = models.CharField(max_length=10, default="ok")       # ok | denied
    status_code = models.PositiveSmallIntegerField(default=200)
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=255, blank=True)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    parent_id = models.CharField(max_length=40, blank=True)       # e.g. the invoice a payment belongs to
    object_repr = models.CharField(max_length=200, blank=True)    # "ACM-0005", a client's name ...
    changes = models.JSONField(default=dict, blank=True)          # the request body, redacted
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Audit log entries cannot be changed.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_email or '-'} {self.action}"
