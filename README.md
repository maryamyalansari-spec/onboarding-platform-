# Itifaq Onboarding Platform

Legal client intake and conflict check platform for UAE law firms.

---

## Step 1 Setup — Database

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ with the pgvector extension installed
- Redis 7+

### Install pgvector (if not already installed)
```
# On Windows with PostgreSQL installed via installer:
# Download pgvector from https://github.com/pgvector/pgvector
# Or via psql: CREATE EXTENSION vector;
```

### Create the database
```sql
CREATE DATABASE itifaq_onboarding;
```

### Configure environment
```bash
cp .env.example .env
# Edit .env and fill in DATABASE_URL and other values
```

### Install Python dependencies
```bash
pip install -r requirements.txt
```

### Initialise the database
```bash
cd backend
python ../scripts/init_db.py
```

This will:
- Enable the pgvector extension
- Create all tables
- Patch the `name_embedding` column to use the `vector(1536)` type
- Create the HNSW index for fast cosine similarity search
- Seed a demo firm and admin user (admin@demo.ae / admin123)

### Run the Flask app (development)
```bash
cd backend
python app.py
```

---

## Build Progress

- [x] Step 1 — PostgreSQL database setup with all tables and pgvector
- [x] Step 2 — Flask app skeleton with all routes stubbed
- [x] Step 3 — Admin login and session authentication
- [x] Step 4 — Frontend CSS design system
- [x] Step 5 — Admin dashboard
- [x] Step 6 — Client portal Step 1: contact info
- [x] Step 7 — Client portal Step 2: ID upload
- [x] Step 8 — Client portal Step 3: voice recorder
- [x] Step 9 — Client portal Step 4: documents
- [x] Step 10 — Confirmation screen
- [x] Step 11 — PaddleOCR integration
- [x] Step 12 — Conflict check logic
- [x] Step 13 — Twilio WhatsApp webhook
- [x] Step 14 — Whisper STT integration
- [x] Step 15 — WhatsApp transcription edit flow
- [x] Step 16 — GPT-4 brief generation
- [x] Step 17 — AI brief display on admin
- [x] Step 18 — Document naming and file management
- [x] Step 19 — Firm-requested document checklist
- [x] Step 20 — Client portal login via token
- [x] Step 21 — Client edit flow + re-conflict-check trigger
- [x] Step 22 — Engagement letter PDF generation
- [x] Step 23 — DocuSeal e-signature
- [x] Step 24 — Calendly integration
- [x] Step 25 — SendGrid email notifications
- [x] Step 26 — KYC step
- [x] Step 27 — Full audit logging
- [x] Step 28 — Dark/light mode testing
- [x] Step 29 — End-to-end flow testing

---

## Running Tests

Tests use an in-memory SQLite database — no PostgreSQL or Redis required.

```bash
pip install pytest pytest-flask coverage
pytest
```

Run with coverage:
```bash
coverage run -m pytest
coverage report -m
```

> **Note:** Tests that hit PaddleOCR, Whisper, or pgvector-specific SQL
> (vector similarity, `<=>`) are skipped or gracefully catch missing
> dependencies, so the suite runs cleanly without AI APIs configured.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis for Celery tasks |
| `OPENAI_API_KEY` | GPT-4o, Whisper, embeddings |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_NUMBER` | WhatsApp |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | Email notifications |
| `DOCUSEAL_URL` / `DOCUSEAL_API_KEY` | E-signature (self-hosted DocuSeal) |
| `CALENDLY_LINK` / `CALENDLY_WEBHOOK_SECRET` | Booking integration |
| `PORTAL_BASE_URL` | Public URL (e.g. `https://onboarding.yourfirm.ae`) |

---

## Git Setup

```bash
cd "itifaq onboarding platform"
git init
git add .
git commit -m "Initial commit — complete platform Steps 1–29"
```
