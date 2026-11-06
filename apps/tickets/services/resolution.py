
from rest_framework.exceptions import ValidationError

from apps.tickets.models import Ticket


def validate_resolution_details(
    *,
    resolution_type=None,
    resolution_notes=None,
):
    valid_types = {
        value for value, _ in Ticket.ResolutionType.choices
    }

    if resolution_type not in valid_types:
        raise ValidationError({
            "resolution_type": "Select a valid resolution type."
        })

    if not isinstance(resolution_notes, str) or not resolution_notes.strip():
        raise ValidationError({
            "resolution_notes": "Resolution notes are required."
        })

    return resolution_type, resolution_notes.strip()


def validate_reopen_reason(reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({
            "reason": "A reason is required to reopen this ticket."
        })

    return reason.strip()


def apply_resolution(*, ticket, user, now, resolution_type, resolution_notes):
    resolution_type, resolution_notes = validate_resolution_details(
        resolution_type=resolution_type,
        resolution_notes=resolution_notes,
    )

    ticket.resolution_type = resolution_type
    ticket.resolution_notes = resolution_notes
    ticket.resolved_at = now
    ticket.resolved_by = user

    return ticket


def apply_closure(*, ticket, user, now):
    if not ticket.resolved_at or not ticket.resolved_by_id:
        raise ValidationError({
            "status": "The ticket must have valid resolution details before closure."
        })

    if not ticket.resolution_type or not ticket.resolution_notes.strip():
        raise ValidationError({
            "status": "Resolution type and notes are required before closure."
        })

    ticket.closed_at = now
    ticket.closed_by = user
    ticket.resolution_confirmed_at = now
    ticket.resolution_confirmed_by = user

    return ticket


def apply_reopen(*, ticket):
    ticket.reopen_count += 1

    # Clear only the current resolution cycle.
    # Historical values must be captured in the activity
    # log before this function is called.
    ticket.resolution_type = None
    ticket.resolution_notes = ""
    ticket.resolved_at = None
    ticket.resolved_by = None
    ticket.closed_at = None
    ticket.closed_by = None
    ticket.resolution_confirmed_at = None
    ticket.resolution_confirmed_by = None

    return ticket
