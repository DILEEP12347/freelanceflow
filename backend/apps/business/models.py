from django.db import models


class BusinessProfile(models.Model):
    """One row per tenant (it lives in the tenant's own schema, always pk=1).
    Invoices will pull their header, currency and numbering prefix from here."""

    business_name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, default="India")
    tax_id = models.CharField(max_length=50, blank=True)  # GSTIN / VAT number
    default_currency = models.CharField(max_length=3, default="INR")
    invoice_prefix = models.CharField(max_length=10, default="INV")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name
