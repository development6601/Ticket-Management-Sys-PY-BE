
from django.urls import path

from apps.tickets.api.views.categories import *


urlpatterns = [
    path("ticket-categories/", TicketCategoryListView.as_view(), name="ticket-category-list"),
    path("ticket-categories/<uuid:id>/", TicketCategoryDetailView.as_view(), name="ticket-category-detail"),
]
