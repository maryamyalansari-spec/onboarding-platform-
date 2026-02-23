"""
conftest.py — Pytest fixtures for Itifaq Onboarding Platform.

Uses an in-memory SQLite database so no PostgreSQL connection is needed.
pgvector-specific SQL (CAST … AS vector, <=>) is not available in SQLite,
so any test that hits conflict-check or embedding code must be skipped or
mocked. All other routes work fine.
"""

import os
import sys
import pytest

# Put backend on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ.setdefault("FLASK_ENV",         "testing")
os.environ.setdefault("FLASK_SECRET_KEY",  "test-secret")
os.environ.setdefault("DATABASE_URL",      "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL",         "redis://localhost:6379/0")
os.environ.setdefault("PORTAL_BASE_URL",   "http://localhost")


@pytest.fixture(scope="session")
def app():
    """Create application with an in-memory SQLite database."""
    from flask import Flask
    from database import db
    from config import Config

    class TestConfig(Config):
        TESTING                          = True
        SQLALCHEMY_DATABASE_URI          = "sqlite:///:memory:"
        WTF_CSRF_ENABLED                 = False
        SESSION_TYPE                     = "filesystem"   # avoid Redis dependency
        UPLOAD_FOLDER                    = "/tmp/itifaq_test_uploads"
        SENDGRID_API_KEY                 = ""
        TWILIO_ACCOUNT_SID               = ""
        TWILIO_AUTH_TOKEN                = ""
        OPENAI_API_KEY                   = ""

    test_app = Flask(
        __name__,
        template_folder=os.path.join(
            os.path.dirname(__file__), "..", "frontend"
        ),
        static_folder=os.path.join(
            os.path.dirname(__file__), "..", "frontend", "static"
        ),
    )
    test_app.config.from_object(TestConfig)
    db.init_app(test_app)

    # Register blueprints
    with test_app.app_context():
        from routes.auth      import auth_bp
        from routes.admin     import admin_bp
        from routes.client    import client_bp
        from routes.conflict  import conflict_bp
        from routes.ocr       import ocr_bp
        from routes.documents import documents_bp

        test_app.register_blueprint(auth_bp,      url_prefix="/auth")
        test_app.register_blueprint(admin_bp,     url_prefix="/admin")
        test_app.register_blueprint(client_bp,    url_prefix="/client")
        test_app.register_blueprint(conflict_bp,  url_prefix="/api/conflict")
        test_app.register_blueprint(ocr_bp,       url_prefix="/api/ocr")
        test_app.register_blueprint(documents_bp, url_prefix="/api/documents")

        # Create all tables
        import models  # noqa
        db.create_all()

        # Seed a test firm + admin user
        from models import LawFirm, User, UserRole
        from werkzeug.security import generate_password_hash
        import uuid

        firm = LawFirm(firm_id=str(uuid.uuid4()), firm_name="Test Firm")
        db.session.add(firm)

        admin = User(
            user_id=str(uuid.uuid4()),
            firm_id=firm.firm_id,
            name="Test Admin",
            email="admin@test.ae",
            password_hash=generate_password_hash("testpass"),
            role=UserRole.admin,
        )
        db.session.add(admin)
        db.session.commit()

        # Store on the app so fixtures can access them
        test_app._test_firm_id  = firm.firm_id
        test_app._test_admin_id = admin.user_id

    os.makedirs(TestConfig.UPLOAD_FOLDER, exist_ok=True)
    yield test_app


@pytest.fixture(scope="session")
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def firm_id(app):
    return app._test_firm_id


@pytest.fixture(scope="session")
def admin_client(app):
    """Authenticated admin test client."""
    with app.test_client() as c:
        with app.app_context():
            with c.session_transaction() as sess:
                sess["user_id"] = app._test_admin_id
                sess["firm_id"] = app._test_firm_id
                sess["role"]    = "admin"
                sess["name"]    = "Test Admin"
        yield c
