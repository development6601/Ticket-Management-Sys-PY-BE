from django.urls import path

from apps.tickets.api.views.categories import *
from apps.tickets.api.views.assignments import *
from apps.tickets.api.views.tickets import *
from apps.tickets.api.views.transitions import (
    TicketAvailableTransitionsView,
    TicketTransitionView,
)

urlpatterns = [
    path("ticket-categories/", TicketCategoryListView.as_view(), name="ticket-category-list"),
    path("ticket-categories/<uuid:id>/", TicketCategoryDetailView.as_view(), name="ticket-category-detail"),
    path("tickets/<uuid:ticket_id>/assignment/", TicketAssignmentView.as_view(), name="ticket-assignment"),
    path("tickets/", TicketCreateView.as_view(), name="ticket-create"),

    path("tickets/<str:ticket_number>/transitions/", TicketAvailableTransitionsView.as_view(), name="ticket-available-transitions"),
    path("tickets/<str:ticket_number>/transition/", TicketTransitionView.as_view(), name="ticket-transition"),
    path("tickets/<str:ticket_number>/", TicketDetailUpdateView.as_view(), name="ticket-detail-update"),
]
