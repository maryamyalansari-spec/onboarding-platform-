"""
ocr.py — PaddleOCR processing routes.

OCR output is always normalised into the conflict check JSON schema
via utils/conflict_schema.py before being saved or checked.
"""

import os
import logging
from flask import Blueprint, request, send_file, current_app, g

from database import db
from models import Passport, EmiratesID, Client
from utils.response import success, error, not_found
from utils.auth import client_token_auth
from utils.conflict_schema import normalise_ocr_output

ocr_bp = Blueprint("ocr", __name__)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Synchronous OCR endpoints (called directly, not via Celery)
#  Used for on-demand re-processing or admin triggering.
# ════════════════════════════════════════════════════════════

@ocr_bp.route("/passport", methods=["POST"])
def process_passport():
    """
    POST /api/ocr/passport
    Body JSON: { "passport_id": str }

    Runs PaddleOCR synchronously on the passport image.
    Updates the Passport record and returns extracted fields + conflict payload.
    """
    body = request.get_json() or {}
    passport_id = body.get("passport_id")

    if not passport_id:
        return error("passport_id required.", 400)

    passport = Passport.query.get(passport_id)
    if not passport:
        return not_found("Passport")

    if not os.path.exists(passport.image_path):
        return error("Passport image file not found on server.", 404)

    try:
        from utils.ocr import extract_text_blocks, extract_passport_fields
        texts  = extract_text_blocks(passport.image_path)
        fields = extract_passport_fields(texts)
    except ImportError:
        return error("PaddleOCR is not installed. Run: pip install paddleocr paddlepaddle", 503)
    except Exception as exc:
        logger.exception(f"OCR failed for passport {passport_id}: {exc}")
        return error(f"OCR processing failed: {exc}", 500)

    # Persist to DB
    passport.passport_number = fields.get("passport_number")
    passport.nationality     = fields.get("nationality")
    passport.date_of_birth   = fields.get("date_of_birth")
    passport.expiry_date     = fields.get("expiry_date")
    passport.ocr_raw         = {**fields, "raw_texts": texts}
    db.session.commit()

    # Build conflict payload
    ocr_raw_for_schema = {
        "full_name":       fields.get("full_name"),
        "passport_number": fields.get("passport_number"),
        "nationality":     fields.get("nationality"),
    }
    conflict_payload = normalise_ocr_output(ocr_raw_for_schema, source_file=passport.image_path)

    return success(data={
        "passport_id":      passport_id,
        "extracted":        fields,
        "conflict_payload": conflict_payload,
    })


@ocr_bp.route("/emirates-id", methods=["POST"])
def process_emirates_id():
    """
    POST /api/ocr/emirates-id
    Body JSON: { "id_record_id": str }

    Runs PaddleOCR synchronously on an Emirates ID image.
    """
    body = request.get_json() or {}
    id_record_id = body.get("id_record_id")

    if not id_record_id:
        return error("id_record_id required.", 400)

    eid = EmiratesID.query.get(id_record_id)
    if not eid:
        return not_found("Emirates ID record")

    if not os.path.exists(eid.image_path):
        return error("Emirates ID image file not found on server.", 404)

    try:
        from utils.ocr import extract_text_blocks, extract_emirates_id_fields
        texts  = extract_text_blocks(eid.image_path)
        fields = extract_emirates_id_fields(texts)
    except ImportError:
        return error("PaddleOCR is not installed. Run: pip install paddleocr paddlepaddle", 503)
    except Exception as exc:
        logger.exception(f"OCR failed for Emirates ID {id_record_id}: {exc}")
        return error(f"OCR processing failed: {exc}", 500)

    # Persist
    eid.id_number = fields.get("id_number")
    eid.ocr_raw   = {**fields, "raw_texts": texts}
    db.session.commit()

    # Build conflict payload
    ocr_raw_for_schema = {
        "full_name":   fields.get("full_name"),
        "id_number":   fields.get("id_number"),
        "nationality": fields.get("nationality"),
    }
    conflict_payload = normalise_ocr_output(ocr_raw_for_schema, source_file=eid.image_path)

    return success(data={
        "id_record_id":    id_record_id,
        "extracted":       fields,
        "conflict_payload": conflict_payload,
    })


# ════════════════════════════════════════════════════════════
#  Async task status polling
# ════════════════════════════════════════════════════════════

@ocr_bp.route("/status/<task_id>", methods=["GET"])
def ocr_status(task_id):
    """
    GET /api/ocr/status/<task_id>
    Poll for a Celery OCR task status.
    """
    try:
        from tasks.celery_app import celery
        from celery.result import AsyncResult
        result = AsyncResult(task_id, app=celery)

        status_map = {
            "PENDING":  "pending",
            "STARTED":  "processing",
            "SUCCESS":  "done",
            "FAILURE":  "failed",
            "RETRY":    "processing",
            "REVOKED":  "failed",
        }

        state  = status_map.get(result.state, "pending")
        output = result.result if result.state == "SUCCESS" else None
        err_msg = str(result.result) if result.state == "FAILURE" else None

        return success(data={
            "task_id": task_id,
            "status":  state,
            "result":  output,
            "error":   err_msg,
        })
    except Exception as exc:
        return success(data={
            "task_id": task_id,
            "status":  "unknown",
            "result":  None,
            "error":   str(exc),
        })


# ════════════════════════════════════════════════════════════
#  Admin trigger — re-run OCR on all pending docs for a client
# ════════════════════════════════════════════════════════════

@ocr_bp.route("/run/<client_id>", methods=["POST"])
def trigger_ocr_for_client(client_id):
    """
    POST /api/ocr/run/<client_id>
    Admin route — queue OCR Celery tasks for all uploaded passport/ID images.
    Returns a list of Celery task IDs.
    """
    from utils.auth import login_required
    from models import Client

    client = Client.query.get(client_id)
    if not client:
        return not_found("Client")

    from tasks.process_docs import run_ocr
    task_ids = []

    for passport in client.passports:
        if os.path.exists(passport.image_path):
            task = run_ocr.delay(
                client_id     = client_id,
                document_type = "passport",
                file_path     = passport.image_path,
                record_id     = passport.passport_id,
            )
            task_ids.append({"type": "passport", "record_id": passport.passport_id, "task_id": task.id})

    for eid in client.emirates_ids:
        if os.path.exists(eid.image_path):
            task = run_ocr.delay(
                client_id     = client_id,
                document_type = "emirates_id",
                file_path     = eid.image_path,
                record_id     = eid.id_record_id,
            )
            task_ids.append({"type": "emirates_id", "record_id": eid.id_record_id, "task_id": task.id})

    return success(data={"queued": task_ids, "count": len(task_ids)})


# ════════════════════════════════════════════════════════════
#  Image preview endpoints (serve uploaded document images)
# ════════════════════════════════════════════════════════════

@ocr_bp.route("/preview/passport/<passport_id>", methods=["GET"])
def preview_passport(passport_id):
    """
    GET /api/ocr/preview/passport/<passport_id>?token=<portal_token>
    Serve the uploaded passport image for preview in the client portal.
    """
    passport = Passport.query.get(passport_id)
    if not passport:
        return not_found("Passport")

    # Verify token ownership
    token = request.args.get("token", "")
    if token:
        from models import Client
        from datetime import datetime, timezone
        client = Client.query.filter_by(portal_token=token).first()
        if not client or client.client_id != passport.client_id:
            from utils.response import forbidden
            return forbidden("Access denied.")
        if client.token_expires_at and client.token_expires_at < datetime.now(timezone.utc):
            from utils.response import unauthorized
            return unauthorized("Token expired.")

    if not os.path.exists(passport.image_path):
        return not_found("Image file")

    return send_file(passport.image_path)


@ocr_bp.route("/preview/eid/<id_record_id>", methods=["GET"])
def preview_eid(id_record_id):
    """
    GET /api/ocr/preview/eid/<id_record_id>?token=<portal_token>
    Serve the uploaded Emirates ID image.
    """
    eid = EmiratesID.query.get(id_record_id)
    if not eid:
        return not_found("Emirates ID")

    token = request.args.get("token", "")
    if token:
        from models import Client
        from datetime import datetime, timezone
        client = Client.query.filter_by(portal_token=token).first()
        if not client or client.client_id != eid.client_id:
            from utils.response import forbidden
            return forbidden("Access denied.")
        if client.token_expires_at and client.token_expires_at < datetime.now(timezone.utc):
            from utils.response import unauthorized
            return unauthorized("Token expired.")

    if not os.path.exists(eid.image_path):
        return not_found("Image file")

    return send_file(eid.image_path)
