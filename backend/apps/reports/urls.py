from django.urls import path

from .views import ClientsReportView, ProfitLossReportView, ReceivablesReportView, RevenueReportView, TimeReportView

urlpatterns = [
    path("reports/revenue/", RevenueReportView.as_view()),
    path("reports/receivables/", ReceivablesReportView.as_view()),
    path("reports/profit-loss/", ProfitLossReportView.as_view()),
    path("reports/clients/", ClientsReportView.as_view()),
    path("reports/time/", TimeReportView.as_view()),
]
