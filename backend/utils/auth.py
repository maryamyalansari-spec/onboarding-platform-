"""
auth.py — Authentication and authorisation decorators + helpers.
"""

from functools import wraps
from datetime import datetime, timezone
from flask import session, request, g, current_app
from utils.response import unauthorized, forbidden


# ─── Decorators ───────────────────────────────────────────────────────────────

def login_required(f):
    """Require a valid admin or lawyer session. Returns 401 otherwise."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            # API request → JSON error; browser request → can redirect
            if _wants_json():
                return unauthorized("You must be logged in.")
            from flask import redirect, url_for
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require role == admin. Returns 401/403 otherwise."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if _wants_json():
                return unauthorized("You must be logged in.")
            from flask import redirect, url_for
            return redirect(url_for("auth.login_page"))
        if session.get("role") != "admin":
            return forbidden("Admin access required.")
        return f(*args, **kwargs)
    return decorated


def client_token_auth(f):
    """
    Validate a client's portal token from query string (?token=...).
    Attaches the client object to flask.g as g.client on success.
    Returns 401 if token missing or invalid / expired.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get("token") or (request.get_json() or {}).get("portal_token")
        if not token:
            return unauthorized("Portal token required.")

        client = _validate_client_token(token)
        if client is None:
            return unauthorized("Invalid or expired portal token.")

        g.client = client
        return f(*args, **kwargs)
    return decorated


# ─── Session helpers ──────────────────────────────────────────────────────────

def get_current_user() -> dict | None:
    """Return current user dict from session. None if not logged in."""
    if not session.get("user_id"):
        return None
    return {
        "user_id":   session["user_id"],
        "firm_id":   session["firm_id"],
        "firm_name": session.get("firm_name"),
        "role":      session["role"],
        "name":      session["name"],
        "email":     session.get("email"),
    }


def get_current_firm_id() -> str | None:
    """Return firm_id from session. ALL DB queries must filter by this."""
    return session.get("firm_id")


# ─── Token validation ─────────────────────────────────────────────────────────

def _validate_client_token(token: str):
    """
    Look up a client by portal_token.
    Returns the Client model instance if valid and not expired, else None.
    """
    try:
        from models import Client
        client = Client.query.filter_by(portal_token=token).first()
        if not client:
            return None
        # Check expiry
        if client.token_expires_at:
            if datetime.now(timezone.utc) > client.token_expires_at:
                current_app.logger.info(f"Expired token used for client {client.reference_id}")
                return None
        return client
    except Exception as e:
        current_app.logger.warning(f"Token validation error: {e}")
        return None


# ─── Private helpers ──────────────────────────────────────────────────────────

def _wants_json() -> bool:
    """True if the request expects a JSON response (API call, not browser nav)."""
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" or request.is_json or request.path.startswith("/api/")
