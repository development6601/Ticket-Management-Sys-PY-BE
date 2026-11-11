from rest_framework.exceptions import ValidationError

from apps.support.models import SupportTeam

def get_ticket_support_team(*, customer, category):
    """
    Resolving the support team for a new ticket.

    Routing priority:
    1. Category-specific support team.
    2. Company's default support team.
    3. None (unassigned routing queue).
    """

    if category.customer_id != customer.pk:
        raise ValidationError({
            "category": (
                "The selected category does not belong "
                "to this company."
            )
        })

    if not category.is_active:
        raise ValidationError({
            "category": "The selected category is inactive."
        })

    if category.default_support_team_id:
        team = category.default_support_team

        if not team.is_active:
            raise ValidationError({
                "category": (
                    "The category's configured support team "
                    "is inactive. Please update the routing "
                    "configuration."
                )
            })

        return team

    if customer.default_support_team_id:
        team = customer.default_support_team

        if not team.is_active:
            raise ValidationError({
                "default_support_team": (
                    "The company's default support team "
                    "is inactive."
                )
            })

        return team

    return None