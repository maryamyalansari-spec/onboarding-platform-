"""
response.py — Consistent JSON response helpers used across all routes.
"""

from flask import jsonify


def success(data=None, message="OK", status_code=200):
    payload = {"success": True, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status_code


def created(data=None, message="Created"):
    return success(data=data, message=message, status_code=201)


def error(message="An error occurred", status_code=400, details=None):
    payload = {"success": False, "error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status_code


def not_found(resource="Resource"):
    return error(f"{resource} not found.", status_code=404)


def forbidden(message="Access denied."):
    return error(message, status_code=403)


def server_error(message="Internal server error."):
    return error(message, status_code=500)


def unauthorized(message="Authentication required."):
    return error(message, status_code=401)
