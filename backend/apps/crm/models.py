from django.db import models


class ClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(max_length=7, default="#6b7280")  # #rrggbb

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Client(models.Model):
    """Lives in EACH tenant's schema. No tenant FK needed: the schema is the isolation."""

    name = models.CharField(max_length=200)
    company_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)

    # Billing details: Week 4 invoices read these.
    tax_id = models.CharField(max_length=50, blank=True)  # GSTIN / VAT number
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=14)

    status = models.CharField(max_length=20, choices=ClientStatus.choices, default=ClientStatus.ACTIVE)
    tags = models.ManyToManyField(Tag, blank=True, related_name="clients")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class Contact(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    job_title = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.client})"


class Note(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="notes")
    body = models.TextField()
    # Plain values instead of a foreign key to the user: users live in the public schema,
    # and a note should survive its author being deleted.
    author_id = models.BigIntegerField(null=True, blank=True)
    author_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LeadStage(models.TextChoices):
    LEAD = "lead", "Lead"
    CONTACTED = "contacted", "Contacted"
    PROPOSAL = "proposal", "Proposal"
    WON = "won", "Won"
    LOST = "lost", "Lost"


OPEN_STAGES = (LeadStage.LEAD.value, LeadStage.CONTACTED.value, LeadStage.PROPOSAL.value)
CLOSED_STAGES = (LeadStage.WON.value, LeadStage.LOST.value)


class Lead(models.Model):
    title = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    company_name = models.CharField(max_length=200, blank=True)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads")

    stage = models.CharField(max_length=20, choices=LeadStage.choices, default=LeadStage.LEAD)
    # Money is stored as an integer in minor units (paise/cents): 50000.00 INR -> 5000000. Never floats.
    value_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="INR")
    expected_close_date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class ActivityKind(models.TextChoices):
    CLIENT_CREATED = "client_created", "Client created"
    CLIENT_ARCHIVED = "client_archived", "Client archived"
    CLIENT_RESTORED = "client_restored", "Client restored"
    CONTACT_ADDED = "contact_added", "Contact added"
    NOTE_ADDED = "note_added", "Note added"
    LEAD_CREATED = "lead_created", "Lead created"
    STAGE_CHANGED = "stage_changed", "Stage changed"
    LEAD_CONVERTED = "lead_converted", "Lead converted"


class Activity(models.Model):
    """Append-only timeline entry. Week 4+ adds invoice and payment events here."""

    kind = models.CharField(max_length=30, choices=ActivityKind.choices)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "activities"
