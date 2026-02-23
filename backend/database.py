import os
import psycopg2
import psycopg2.extras
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

db = SQLAlchemy()


def init_db(app):
    """Bind SQLAlchemy to the Flask app and create all tables."""
    db.init_app(app)
    with app.app_context():
        # Enable pgvector extension before creating tables
        _enable_pgvector(app)
        db.create_all()
        print("[DB] All tables created successfully.")


def _enable_pgvector(app):
    """Create the pgvector extension if it does not already exist."""
    with app.app_context():
        try:
            db.session.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            db.session.commit()
            print("[DB] pgvector extension enabled.")
        except Exception as e:
            db.session.rollback()
            print(f"[DB] Warning: could not enable pgvector extension: {e}")


def get_raw_connection():
    """
    Return a raw psycopg2 connection for operations that need
    cursor-level control (e.g. COPY, bulk inserts, vector queries).
    """
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:password@localhost:5432/itifaq_onboarding"
    )
    conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def execute_raw(sql, params=None):
    """Execute a raw SQL statement and return all rows as dicts."""
    conn = get_raw_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            try:
                return cur.fetchall()
            except psycopg2.ProgrammingError:
                return []
    finally:
        conn.close()
