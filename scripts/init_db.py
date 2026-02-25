"""
init_db.py — One-time database initialization script.

Run this once to:
  1. Create the pgvector extension
  2. Create all tables via SQLAlchemy
  3. Patch the conflict_index.name_embedding column to use the vector type
  4. Create the HNSW index for fast cosine similarity search

Usage:
    cd itifaq-onboarding
    python scripts/init_db.py
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from flask import Flask
from config import get_config
from database import db, _enable_pgvector
from models import *   # noqa: F401,F403  — imports all models so SQLAlchemy is aware of them
import psycopg2


VECTOR_DIMENSIONS = 1536   # OpenAI text-embedding-3-small / ada-002 output size


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())
    db.init_app(app)
    return app


def patch_vector_column(conn):
    """
    SQLAlchemy declares name_embedding as Text.
    After create_all(), we ALTER the column to use the pgvector vector type.
    This is idempotent — it checks before altering.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = 'conflict_index'
          AND column_name = 'name_embedding';
    """)
    row = cur.fetchone()
    if row and row[0] != "USER-DEFINED":
        print(f"[DB] Patching name_embedding column (currently '{row[0]}') to vector({VECTOR_DIMENSIONS})...")
        cur.execute(f"""
            ALTER TABLE conflict_index
            ALTER COLUMN name_embedding TYPE vector({VECTOR_DIMENSIONS})
            USING name_embedding::vector;
        """)
        conn.commit()
        print("[DB] name_embedding column patched to vector type.")
    elif row and row[0] == "USER-DEFINED":
        print("[DB] name_embedding is already a vector column — skipping patch.")
    else:
        print("[DB] Warning: name_embedding column not found.")
    cur.close()


def create_vector_index(conn):
    """
    Create an HNSW index on name_embedding for fast approximate nearest-neighbour
    cosine similarity search. Idempotent — skips if index already exists.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'conflict_index'
          AND indexname = 'ix_conflict_index_name_embedding_hnsw';
    """)
    if cur.fetchone():
        print("[DB] HNSW index already exists — skipping.")
    else:
        print("[DB] Creating HNSW vector index on conflict_index.name_embedding...")
        cur.execute(f"""
            CREATE INDEX ix_conflict_index_name_embedding_hnsw
            ON conflict_index
            USING hnsw (name_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
        """)
        conn.commit()
        print("[DB] HNSW index created.")
    cur.close()


def seed_demo_firm(conn):
    """
    Insert a default demo law firm and admin user if they don't exist.
    Useful for first-time setup.
    """
    from werkzeug.security import generate_password_hash
    import uuid as _uuid

    cur = conn.cursor()
    cur.execute("SELECT firm_id FROM law_firms WHERE firm_name = 'Demo Firm';")
    if cur.fetchone():
        print("[SEED] Demo firm already exists — skipping seed.")
        cur.close()
        return

    firm_id = str(_uuid.uuid4())
    user_id = str(_uuid.uuid4())
    password_hash = generate_password_hash("admin123")

    cur.execute(
        "INSERT INTO law_firms (firm_id, firm_name, created_at) VALUES (%s, %s, NOW());",
        (firm_id, "Demo Firm")
    )
    cur.execute(
        """INSERT INTO users (user_id, firm_id, name, email, password_hash, role, is_active, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, TRUE, NOW());""",
        (user_id, firm_id, "Demo Admin", "admin@demo.ae", password_hash, "admin")
    )
    conn.commit()
    cur.close()
    print(f"[SEED] Demo firm created (firm_id={firm_id})")
    print(f"[SEED] Admin user: admin@demo.ae / admin123")
    print("[SEED] IMPORTANT: Change the password immediately in production.")


def main():
    print("=" * 60)
    print(" Itifaq Onboarding Platform — Database Initialisation")
    print("=" * 60)

    app = create_app()

    with app.app_context():
        # Step 1: Enable pgvector extension
        _enable_pgvector(app)

        # Step 2: Create all tables
        print("[DB] Creating all tables...")
        db.create_all()
        print("[DB] Tables created.")

    # Step 3: Patch vector column and create index (needs raw psycopg2)
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:password@localhost:5432/itifaq_onboarding"
    )
    conn = psycopg2.connect(db_url)

    try:
        try:
            patch_vector_column(conn)
            create_vector_index(conn)
        except Exception as e:
            conn.rollback()
            print(f"[DB] Skipping vector setup (pgvector not installed): {e}")
        seed_demo_firm(conn)
    finally:
        conn.close()

    print("=" * 60)
    print(" Database initialisation complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
