from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"companies", views.CompanyViewSet, basename="quotation-company")
router.register(r"contacts", views.CompanyContactViewSet, basename="quotation-contact")
router.register(r"items", views.QuoteItemViewSet, basename="quotation-item")
router.register(r"inquiries", views.InquiryViewSet, basename="quotation-inquiry")
router.register(r"inquiry-lines", views.InquiryLineViewSet, basename="quotation-inquiry-line")
router.register(r"historical-imports", views.HistoricalPriceImportViewSet, basename="quotation-historical-import")
router.register(r"historical-import-lines", views.HistoricalPriceImportLineViewSet, basename="quotation-historical-import-line")
router.register(r"quotes", views.QuotationViewSet, basename="quotation")
router.register(r"quote-lines", views.QuotationLineViewSet, basename="quotation-line")
router.register(r"price-history", views.CompanyPriceHistoryViewSet, basename="quotation-price-history")
router.register(r"audit-logs", views.QuotationAuditLogViewSet, basename="quotation-audit-log")

urlpatterns = [
    path("settings/", views.QuotationSettingsView.as_view(), name="quotation-settings"),
    path("", include(router.urls)),
]
