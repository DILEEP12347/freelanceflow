from django.db import models
from django.utils import timezone


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class Project(models.Model):
    """Something you track time against. Every project belongs to one client."""

    name = models.CharField(max_length=120)
    client = models.ForeignKey("crm.Client", on_delete=models.PROTECT, related_name="projects")
    hourly_rate_minor = models.BigIntegerField(default=0)  # default rate, copied onto each time entry
    currency = models.CharField(max_length=3, default="INR")
    billable = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class TimeEntry(models.Model):
    """A stretch of work. `ended_at` is null while the timer is running."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="time_entries")
    user_id = models.BigIntegerField()
    user_email = models.CharField(max_length=254, blank=True)
    description = models.CharField(max_length=300, blank=True)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)  # filled in when the entry is stopped
    billable = models.BooleanField(default=True)
    # The rate is COPIED from the project when the entry is made, so changing a project's rate later
    # never rewrites work that was already logged.
    hourly_rate_minor = models.BigIntegerField(default=0)
    # Set when the entry is put on an invoice. If that draft invoice is deleted, this goes back to null
    # and the entry is billable again.
    invoice = models.ForeignKey("invoicing.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="time_entries")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["user_id", "ended_at"]), models.Index(fields=["started_at"])]

    @property
    def is_running(self) -> bool:
        return self.ended_at is None

    @property
    def current_seconds(self) -> int:
        """Stored duration, or the time elapsed so far while the timer is still running."""
        if self.ended_at is None:
            return max(0, int((timezone.now() - self.started_at).total_seconds()))
        return self.duration_seconds


class Expense(models.Model):
    """Money spent. Optionally tied to a client or project, and optionally re-billed to the client."""

    description = models.CharField(max_length=300)
    vendor = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=60, default="General")
    amount_minor = models.BigIntegerField()          # the total paid, tax included
    tax_minor = models.BigIntegerField(default=0)    # the GST inside that total (input tax credit)
    currency = models.CharField(max_length=3, default="INR")
    incurred_on = models.DateField()
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    client = models.ForeignKey("crm.Client", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    billable = models.BooleanField(default=False)    # re-bill this to the client
    invoice = models.ForeignKey("invoicing.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="expenses")
    notes = models.TextField(blank=True)
    created_by_email = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-incurred_on", "-id"]
        indexes = [models.Index(fields=["incurred_on"]), models.Index(fields=["category"])]

    def __str__(self):
        return self.description
