from pathlib import Path

files = [
    "apps/tickets/services/ticket_creation.py",
    "apps/tickets/services/authorization.py",
    "apps/tickets/models.py",
]

keywords = [
    "customer",
    "assigned_team",
    "assigned_employee",
    "TicketAssignment",
    "TicketActivityLog",
    "validate",
    "permission",
]

for filename in files:
    path = Path(filename)
    print(f"\n{'=' * 20} {filename} {'=' * 20}")

    if not path.exists():
        print("FILE MISSING")
        continue

    lines = path.read_text(encoding="utf-8").splitlines()

    for number, line in enumerate(lines, 1):
        if any(word.lower() in line.lower() for word in keywords):
            print(f"{number}: {line}")
