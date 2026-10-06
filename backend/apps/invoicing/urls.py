from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import InvoiceViewSet, PaymentViewSet, TaxRateViewSet

router = DefaultRouter()
router.register("tax-rates", TaxRateViewSet, basename="tax-rate")
router.register("invoices", InvoiceViewSet, basename="invoice")

urlpatterns = [
    path("invoices/<int:invoice_id>/payments/", PaymentViewSet.as_view({"get": "list", "post": "create"})),
    path("invoices/<int:invoice_id>/payments/<int:pk>/", PaymentViewSet.as_view({"delete": "destroy"})),
    path("", include(router.urls)),
]
