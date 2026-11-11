from pathlib import Path
from django.db import transaction
from apps.accounts.models import Permission

print("\n========== 1. CODE CHECK ==========")

checks = {
    "Permission constants": (
        "apps/accounts/permissions/constants.py",
        ["TICKET_ASSIGN_TEAM", "TICKET_ASSIGN_EMPLOYEE"],
    ),
    "Assignment authorization": (
        "apps/tickets/services/authorization.py",
        ["authorize_ticket_assignment"],
    ),
    "Assignment service": (
        "apps/tickets/services/assignment.py",
        ["change_ticket_assignment", "claim_unassigned_ticket"],
    ),
}

for label, (filename, expected) in checks.items():
    path = Path(filename)
    print(f"\n{label}: {'FOUND' if path.exists() else 'MISSING'}")
    if path.exists():
        content = path.read_text(encoding="utf-8")
        for item in expected:
            print(f"  {item}: {'PRESENT' if item in content else 'NOT FOUND'}")

print("\n========== 2. DATABASE PERMISSIONS ==========")

permissions_to_add = [
    {
        "name": "Ticket Assign Team",
        "module": "tickets",
        "action": "assign_team",
        "description": "Assign or change the support team for a ticket.",
    },
    {
        "name": "Ticket Assign Employee",
        "module": "tickets",
        "action": "assign_employee",
        "description": "Assign or change the support employee for a ticket.",
    },
]

with transaction.atomic():
    for item in permissions_to_add:
        existing = Permission.objects.filter(
            module__iexact=item["module"],
            action__iexact=item["action"],
        ).first()

        if existing:
            print(f"ALREADY EXISTS: {existing.module}.{existing.action}")
        else:
            permission = Permission.objects.create(**item)
            print(f"CREATED: {permission.module}.{permission.action}")

print("\n========== 3. FINAL VERIFICATION ==========")

for action in ["assign_team", "assign_employee"]:
    permission = Permission.objects.filter(
        module__iexact="tickets",
        action__iexact=action,
    ).first()
    print(f"{'OK' if permission else 'MISSING'}: tickets.{action}")

print("\n========== COMPLETED ==========")
