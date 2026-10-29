# Service Operations & Ticket Management System

Enterprise-ready Django REST Framework backend for managing service operations, customer support, ticket lifecycles, team assignments, audit logs, and analytics.

---

## 🚀 Tech Stack

- **Framework:** Django 5.1+ & Django REST Framework (DRF)
- **Authentication:** JWT via `djangorestframework-simplejwt`
- **Database:** PostgreSQL (with `psycopg[binary]`)
- **API Documentation:** OpenAPI 3 schema via `drf-spectacular` & Swagger UI
- **Testing:** `pytest` & `pytest-django`

---

## 🛠️ Getting Started

### 1. Clone & Set Up Virtual Environment

```bash
# Clone the repository
git clone <repository-url>
cd Service_Operations

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Variables Configuration

Copy the example environment file and update with your local credentials:

```bash
# Windows PowerShell
Copy-Item .env.example .env

# Linux/macOS
cp .env.example .env
```

Ensure your PostgreSQL database is running and update `DB_NAME`, `DB_USER`, and `DB_PASSWORD` in `.env`.

### 4. Run Migrations & Start Server

```bash
# Apply database migrations
python manage.py migrate

# Create admin user
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

---

## 📖 API Documentation & Endpoints

Interactive documentation is automatically generated via `drf-spectacular`:

- **Swagger UI:** [http://127.0.0.1:8000/api/docs/](http://127.0.0.1:8000/api/docs/)
- **OpenAPI 3 Schema:** [http://127.0.0.1:8000/api/schema/](http://127.0.0.1:8000/api/schema/)
- **API Base Path:** `/api/v1/`

---

## 🧪 Running Tests

Run the full automated test suite using pytest:

```bash
pytest
```

---

## 📂 Project Architecture

```
Service_Operations/
├── apps/
│   ├── accounts/         # Users, roles, permissions, authentication
│   ├── customers/        # Customer organizations and contacts
│   ├── tickets/          # Ticket lifecycle, assignment, notes, attachments
│   ├── teams/            # Teams and member management
│   ├── notifications/    # In-app & email notification dispatch
│   ├── dashboard/        # Metrics, charts, and summary APIs
│   ├── reports/          # Data exports (CSV) and analytics
│   └── settings/         # Dynamic application settings
├── core/                 # Shared base models, mixins, service helpers
├── Service_Operations/   # Project settings, wsgi, asgi, routing
├── tests/                # API and integration test suites
├── manage.py
├── requirements.txt
├── .env.example
└── README.md
```
