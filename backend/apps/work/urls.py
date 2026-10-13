from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ExpenseViewSet, ProjectViewSet, TimeEntryViewSet, WorkInvoiceView

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("time", TimeEntryViewSet, basename="time-entry")
router.register("expenses", ExpenseViewSet, basename="expense")

urlpatterns = [path("work/invoice/", WorkInvoiceView.as_view())] + router.urls
