"""
app.py — Flask application factory for Itifaq Onboarding Platform.
"""

import os
import logging
from datetime import datetime, timezone
from flask import Flask, jsonify, request, g
from config import get_config
from database import db


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "..", "frontend"),
        static_folder=os.path.join(os.path.dirname(__file__), "..", "frontend", "static"),
    )
    app.config.from_object(get_config())

    _configure_logging(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_hooks(app)
    _register_health_check(app)

    return app


# ─── Logging ──────────────────────────────────────────────────────────────────

def _configure_logging(app):
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app.logger.setLevel(level)


# ─── Extensions ───────────────────────────────────────────────────────────────

def _init_extensions(app):
    db.init_app(app)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)


# ─── Blueprints ───────────────────────────────────────────────────────────────

def _register_blueprints(app):
    from routes.auth       import auth_bp
    from routes.admin      import admin_bp
    from routes.client     import client_bp
    from routes.whatsapp   import whatsapp_bp
    from routes.conflict   import conflict_bp
    from routes.ocr        import ocr_bp
    from routes.documents  import documents_bp

    app.register_blueprint(auth_bp,      url_prefix="/auth")
    app.register_blueprint(admin_bp,     url_prefix="/admin")
    app.register_blueprint(client_bp,    url_prefix="/client")
    app.register_blueprint(whatsapp_bp,  url_prefix="/webhook")
    app.register_blueprint(conflict_bp,  url_prefix="/api/conflict")
    app.register_blueprint(ocr_bp,       url_prefix="/api/ocr")
    app.register_blueprint(documents_bp, url_prefix="/api/documents")


# ─── Error handlers ───────────────────────────────────────────────────────────

def _register_error_handlers(app):

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"success": False, "error": "Bad request.", "details": str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"success": False, "error": "Authentication required."}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"success": False, "error": "Access denied."}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "error": f"Route not found: {request.path}"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"success": False, "error": "Method not allowed."}), 405

    @app.errorhandler(413)
    def file_too_large(e):
        return jsonify({"success": False, "error": "File too large. Maximum size is 50 MB."}), 413

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.exception("Unhandled server error")
        return jsonify({"success": False, "error": "Internal server error."}), 500


# ─── Request / response hooks ─────────────────────────────────────────────────

def _register_hooks(app):

    @app.before_request
    def log_request():
        g.request_start = datetime.now(timezone.utc)
        app.logger.debug(f"--> {request.method} {request.path}")

    @app.after_request
    def add_headers(response):
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # CORS — restrict to same origin in production; allow all in dev
        if app.config.get("DEBUG"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"

        # Log response time
        if hasattr(g, "request_start"):
            elapsed = (datetime.now(timezone.utc) - g.request_start).total_seconds() * 1000
            app.logger.debug(f"<-- {response.status_code}  ({elapsed:.1f}ms)")

        return response

    @app.after_request
    def handle_options(response):
        """Allow preflight CORS requests in development."""
        if request.method == "OPTIONS" and app.config.get("DEBUG"):
            response.status_code = 200
        return response


# ─── Health check ─────────────────────────────────────────────────────────────

def _register_health_check(app):

    @app.route("/health")
    def health():
        """
        GET /health
        Returns platform status. Checks DB connectivity.
        """
        try:
            db.session.execute(db.text("SELECT 1"))
            db_status = "ok"
        except Exception as e:
            db_status = f"error: {e}"

        return jsonify({
            "status": "ok" if db_status == "ok" else "degraded",
            "database": db_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "0.1.0",
        }), 200 if db_status == "ok" else 503

    @app.route("/")
    def index():
        return jsonify({
            "platform": "Itifaq Onboarding",
            "status": "running",
            "docs": "/health",
        })


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000, host="0.0.0.0")
