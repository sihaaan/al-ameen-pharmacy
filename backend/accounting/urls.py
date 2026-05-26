from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"customers", views.AccountCustomerViewSet, basename="accounting-customer")
router.register(r"imports", views.AccountingImportViewSet, basename="accounting-import")
router.register(r"import-customers", views.AccountingImportCustomerViewSet, basename="accounting-import-customer")

urlpatterns = [
    path("dashboard/", views.AccountingDashboardView.as_view(), name="accounting-dashboard"),
    path("", include(router.urls)),
]
