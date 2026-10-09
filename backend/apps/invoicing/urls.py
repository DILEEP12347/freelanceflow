from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import InvoiceSettingsView, InvoiceViewSet, PaymentViewSet, RecurringInvoiceViewSet, TaxRateViewSet

router = DefaultRouter()
router.register("tax-rates", TaxRateViewSet, basename="tax-rate")
router.register("invoices", InvoiceViewSet, basename="invoice")
router.register("recurring-invoices", RecurringInvoiceViewSet, basename="recurring-invoice")

urlpatterns = [
    path("invoicing/settings/", InvoiceSettingsView.as_view()),
    path("invoices/<int:invoice_id>/payments/", PaymentViewSet.as_view({"get": "list", "post": "create"})),
    path("invoices/<int:invoice_id>/payments/<int:pk>/", PaymentViewSet.as_view({"delete": "destroy"})),
    path("", include(router.urls)),
]
