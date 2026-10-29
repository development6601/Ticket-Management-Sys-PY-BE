from django.urls import path

from apps.customers.api.views.customers import *

urlpatterns = [
    path("customers/", CustomerListView.as_view(), name="customer-list"),
    path("customers/<uuid:id>/", CustomerDetailView.as_view(), name="customer-detail"),
    path("customers/<uuid:id>/status/", CustomerStatusView.as_view(), name="customer-status"),
    
]