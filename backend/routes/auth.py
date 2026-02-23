"""
auth_routes.py — Login, logout, and session management for admin/lawyer users.
"""

from flask import Blueprint, request, session, redirect, url_for, render_template, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timezone

from database import db
from models import User, LawFirm, AuditLog
from utils.response import success, error, unauthorized
from utils.auth import login_required, admin_required, get_current_user

auth_bp = Blueprint("auth", __name__)


# ─── Login ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET"])
def login_page():
    """Serve the login HTML page."""
    if session.get("user_id"):
        return redirect(url_for("admin.dashboard"))
    return render_template("admin/login.html")


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /auth/login
    Accepts JSON or form data.
    Body: { "email": str, "password": str }

    On success: sets session and returns user info.
    On failure: returns 401.

    Response (success):
    {
        "user": {
            "user_id":   str,
            "firm_id":   str,
            "name":      str,
            "email":     str,
            "role":      str
        },
        "redirect": "/admin/"
    }
    """
    # Accept both JSON and form POST
    if request.is_json:
        body = request.get_json() or {}
    else:
        body = request.form.to_dict()

    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return error("Email and password are required.", 400)

    # Look up user — must be active
    user = User.query.filter_by(email=email, is_active=True).first()

    if not user or not check_password_hash(user.password_hash, password):
        _log_failed_login(email)
        return unauthorized("Invalid email or password.")

    # Verify firm exists
    firm = LawFirm.query.get(user.firm_id)
    if not firm:
        return unauthorized("Firm not found.")

    # Set session
    session.permanent = True
    session["user_id"] = user.user_id
    session["firm_id"] = user.firm_id
    session["firm_name"] = firm.firm_name
    session["role"] = user.role.value
    session["name"] = user.name
    session["email"] = user.email
    session["logged_in_at"] = datetime.now(timezone.utc).isoformat()

    # Audit log
    _write_audit_log(
        firm_id=user.firm_id,
        action=f"User '{user.name}' logged in.",
        performed_by=user.user_id,
        record_type="user",
        record_id=user.user_id,
    )

    return success(data={
        "user": {
            "user_id":   user.user_id,
            "firm_id":   user.firm_id,
            "name":      user.name,
            "email":     user.email,
            "role":      user.role.value,
            "firm_name": firm.firm_name,
        },
        "redirect": "/admin/",
    }, message="Login successful.")


# ─── Logout ───────────────────────────────────────────────────────────────────

@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """
    POST /auth/logout
    Clears session.
    """
    user = get_current_user()

    if user and user.get("user_id"):
        _write_audit_log(
            firm_id=user["firm_id"],
            action=f"User '{user['name']}' logged out.",
            performed_by=user["user_id"],
            record_type="user",
            record_id=user["user_id"],
        )

    session.clear()
    return success(message="Logged out.", data={"redirect": "/auth/login"})


# ─── Session status ───────────────────────────────────────────────────────────

@auth_bp.route("/session", methods=["GET"])
def session_status():
    """
    GET /auth/session
    Returns current session info. Used by frontend to check auth state.

    Response (logged in):
    {
        "authenticated": true,
        "user": { user_id, firm_id, firm_name, name, email, role }
    }

    Response (not logged in):
    {
        "authenticated": false
    }
    """
    if not session.get("user_id"):
        return success(data={"authenticated": False})

    return success(data={
        "authenticated": True,
        "user": {
            "user_id":   session.get("user_id"),
            "firm_id":   session.get("firm_id"),
            "firm_name": session.get("firm_name"),
            "name":      session.get("name"),
            "email":     session.get("email"),
            "role":      session.get("role"),
        },
    })


# ─── User management (admin only) ─────────────────────────────────────────────

@auth_bp.route("/users", methods=["GET"])
@admin_required
def list_users():
    """
    GET /auth/users
    Returns all users for the current firm.

    Response:
    {
        "users": [ { user_id, name, email, role, is_active, created_at } ]
    }
    """
    from utils.auth import get_current_firm_id
    firm_id = get_current_firm_id()
    users = User.query.filter_by(firm_id=firm_id).all()
    return success(data={
        "users": [
            {
                "user_id":    u.user_id,
                "name":       u.name,
                "email":      u.email,
                "role":       u.role.value,
                "is_active":  u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    })


@auth_bp.route("/users", methods=["POST"])
@admin_required
def create_user():
    """
    POST /auth/users
    Body: { "name": str, "email": str, "password": str, "role": "admin" | "lawyer" }
    Creates a new user for the current firm.

    Response:
    {
        "user": { user_id, name, email, role }
    }
    """
    from utils.auth import get_current_firm_id
    body = request.get_json() or {}
    firm_id = get_current_firm_id()

    required = ["name", "email", "password", "role"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return error(f"Missing required fields: {', '.join(missing)}")

    if body["role"] not in ("admin", "lawyer"):
        return error("role must be 'admin' or 'lawyer'.")

    # Check duplicate email
    if User.query.filter_by(email=body["email"].strip().lower()).first():
        return error("A user with this email already exists.", 409)

    import uuid
    from models import UserRole
    user = User(
        user_id=str(uuid.uuid4()),
        firm_id=firm_id,
        name=body["name"].strip(),
        email=body["email"].strip().lower(),
        password_hash=generate_password_hash(body["password"]),
        role=UserRole[body["role"]],
    )
    db.session.add(user)

    _write_audit_log(
        firm_id=firm_id,
        action=f"Admin created new user '{user.name}' ({user.role.value}).",
        performed_by=session.get("user_id"),
        record_type="user",
        record_id=user.user_id,
    )

    db.session.commit()

    return success(data={
        "user": {
            "user_id": user.user_id,
            "name":    user.name,
            "email":   user.email,
            "role":    user.role.value,
        }
    }, message="User created.", status_code=201)


@auth_bp.route("/users/<user_id>/password", methods=["PUT"])
@login_required
def change_password(user_id):
    """
    PUT /auth/users/<user_id>/password
    Body: { "current_password": str, "new_password": str }

    Users can change their own password.
    Admins can change any user's password in their firm.
    """
    from utils.auth import get_current_firm_id
    body = request.get_json() or {}
    current_user = get_current_user()
    firm_id = get_current_firm_id()

    # Can only change own password unless admin
    if current_user["user_id"] != user_id and current_user["role"] != "admin":
        return error("You can only change your own password.", 403)

    user = User.query.filter_by(user_id=user_id, firm_id=firm_id).first()
    if not user:
        return error("User not found.", 404)

    # If changing own password, verify current password
    if current_user["user_id"] == user_id:
        if not check_password_hash(user.password_hash, body.get("current_password", "")):
            return unauthorized("Current password is incorrect.")

    new_password = body.get("new_password", "")
    if len(new_password) < 8:
        return error("New password must be at least 8 characters.")

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    _write_audit_log(
        firm_id=firm_id,
        action=f"Password changed for user '{user.name}'.",
        performed_by=current_user["user_id"],
        record_type="user",
        record_id=user_id,
    )

    return success(message="Password updated successfully.")


@auth_bp.route("/users/<user_id>/deactivate", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    """
    POST /auth/users/<user_id>/deactivate
    Deactivates a user account (soft delete — sets is_active=False).
    Admin cannot deactivate themselves.
    """
    from utils.auth import get_current_firm_id
    firm_id = get_current_firm_id()
    current_user = get_current_user()

    if current_user["user_id"] == user_id:
        return error("You cannot deactivate your own account.")

    user = User.query.filter_by(user_id=user_id, firm_id=firm_id).first()
    if not user:
        return error("User not found.", 404)

    user.is_active = False
    db.session.commit()

    _write_audit_log(
        firm_id=firm_id,
        action=f"User '{user.name}' deactivated.",
        performed_by=current_user["user_id"],
        record_type="user",
        record_id=user_id,
    )

    return success(message=f"User '{user.name}' has been deactivated.")


# ─── Private helpers ──────────────────────────────────────────────────────────

def _write_audit_log(firm_id, action, performed_by=None, record_type=None, record_id=None):
    """Write an entry to the AuditLogs table."""
    import uuid
    try:
        log = AuditLog(
            log_id=str(uuid.uuid4()),
            firm_id=firm_id,
            action=action,
            performed_by=performed_by,
            record_type=record_type,
            record_id=record_id,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        current_app.logger.warning(f"Could not write audit log: {e}")
        db.session.rollback()


def _log_failed_login(email: str):
    """Log a failed login attempt (no firm_id known at this point)."""
    current_app.logger.warning(f"Failed login attempt for email: {email}")
