
from django.urls import path

from apps.tickets.api.views.categories import *
from apps.tickets.api.views.assignments import *
from apps.tickets.api.views.tickets import *

urlpatterns = [
    path("ticket-categories/", TicketCategoryListView.as_view(), name="ticket-category-list"),
    path("ticket-categories/<uuid:id>/", TicketCategoryDetailView.as_view(), name="ticket-category-detail"),
    path("tickets/<uuid:ticket_id>/assignment/", TicketAssignmentView.as_view(), name="ticket-assignment"),
    path("tickets/", TicketCreateView.as_view(), name="ticket-create"),
]
