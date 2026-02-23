"""
conflict.py — Conflict check API routes.

Conflict check flow:
  1. Client uploads passport/ID → OCR extracts data (Step 11)
  2. OCR output normalised to conflict check JSON via conflict_schema.py
  3. Check runs against conflict_index table (3-tier: exact → strong → soft)
  4. Result stored in conflict_results table
  5. Admin notified if score >= 50
"""

import uuid
import logging
from flask import Blueprint, request

from database import db
from models import (
    Client, ConflictResult, ConflictIndex,
    MatchType, ConflictDecision, ClientStatus,
)
from utils.response import success, error, not_found
from utils.auth import login_required, get_current_firm_id
from utils.conflict_schema import normalise_manual_input, validate_payload

conflict_bp = Blueprint("conflict", __name__)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Trigger conflict check for a client
# ════════════════════════════════════════════════════════════

@conflict_bp.route("/check/<client_id>", methods=["POST"])
@login_required
def run_check(client_id):
    """
    POST /api/conflict/check/<client_id>
    Triggers a conflict check via Celery.

    Optional body: override conflict payload if re-checking with new data.
    Otherwise uses the client's existing OCR/passport data.

    Response:
    {
        "task_id":   str,    (Celery task ID — poll /api/ocr/status/<task_id>)
        "client_id": str
    }
    """
    client = Client.query.filter_by(
        client_id=client_id,
        firm_id=get_current_firm_id(),
    ).first()
    if not client:
        return not_found("Client")

    try:
        from tasks.process_docs import run_conflict_check
        task = run_conflict_check.delay(client_id)
        return success(data={"task_id": task.id, "client_id": client_id})
    except Exception as exc:
        # Celery not available — run synchronously
        logger.warning(f"Celery unavailable, running conflict check synchronously: {exc}")
        try:
            from utils.conflict_check import run_conflict_check as _check
            result = _check(client_id)
            return success(data={"task_id": None, "client_id": client_id, "result": result})
        except Exception as exc2:
            return error(f"Conflict check failed: {exc2}", 500)


# ════════════════════════════════════════════════════════════
#  Get latest conflict result for a client
# ════════════════════════════════════════════════════════════

@conflict_bp.route("/result/<client_id>", methods=["GET"])
@login_required
def get_result(client_id):
    """
    GET /api/conflict/result/<client_id>
    Returns the latest conflict check result for a client.
    """
    client = Client.query.filter_by(
        client_id=client_id,
        firm_id=get_current_firm_id(),
    ).first()
    if not client:
        return not_found("Client")

    result = (
        ConflictResult.query
        .filter_by(client_id=client_id)
        .order_by(ConflictResult.created_at.desc())
        .first()
    )

    if not result:
        return success(data={
            "conflict_id":      None,
            "match_type":       "none",
            "confidence_score": 0,
            "decision":         "pending",
            "matched_record":   None,
        })

    matched = None
    if result.matched_record:
        rec = result.matched_record
        matched = {
            "record_id":        rec.record_id,
            "full_name":        rec.full_name,
            "case_type":        rec.case_type,
            "opposing_party":   rec.opposing_party,
            "passport_numbers": rec.passport_numbers or [],
            "nationality":      rec.nationality or [],
        }

    return success(data={
        "conflict_id":      result.conflict_id,
        "match_type":       result.match_type.value,
        "confidence_score": float(result.confidence_score),
        "decision":         result.decision.value,
        "decision_reason":  result.decision_reason,
        "decision_at":      result.decision_at.isoformat() if result.decision_at else None,
        "matched_record":   matched,
        "created_at":       result.created_at.isoformat(),
    })


# ════════════════════════════════════════════════════════════
#  All conflict results for a client (history)
# ════════════════════════════════════════════════════════════

@conflict_bp.route("/history/<client_id>", methods=["GET"])
@login_required
def get_history(client_id):
    """
    GET /api/conflict/history/<client_id>
    Returns all conflict check results for a client (newest first).
    """
    client = Client.query.filter_by(
        client_id=client_id,
        firm_id=get_current_firm_id(),
    ).first()
    if not client:
        return not_found("Client")

    results = (
        ConflictResult.query
        .filter_by(client_id=client_id)
        .order_by(ConflictResult.created_at.desc())
        .all()
    )

    return success(data={
        "results": [
            {
                "conflict_id":      r.conflict_id,
                "match_type":       r.match_type.value,
                "confidence_score": float(r.confidence_score),
                "decision":         r.decision.value,
                "decision_reason":  r.decision_reason,
                "created_at":       r.created_at.isoformat(),
            }
            for r in results
        ]
    })


# ════════════════════════════════════════════════════════════
#  Admin: validate a conflict payload (test tool)
# ════════════════════════════════════════════════════════════

@conflict_bp.route("/validate-payload", methods=["POST"])
def validate():
    """
    POST /api/conflict/validate-payload
    Validates a conflict check payload shape.  Useful for testing OCR output.

    Body: conflict check payload JSON
    Response: { "valid": bool, "errors": [str] }
    """
    body = request.get_json() or {}
    errors = validate_payload(body)
    return success(data={"valid": len(errors) == 0, "errors": errors})


# ════════════════════════════════════════════════════════════
#  Admin: manually run a check with a custom payload
# ════════════════════════════════════════════════════════════

@conflict_bp.route("/check-manual", methods=["POST"])
@login_required
def check_manual():
    """
    POST /api/conflict/check-manual
    Run a conflict check against an ad-hoc payload (not tied to a client record).
    Useful for admin due diligence or testing.

    Body: conflict payload (full_name, passport_numbers, etc.)
    Response: { "match_type", "confidence_score", "matched_record" }
    """
    from utils.conflict_check import (
        _tier1_exact, _tier2_strong, _tier3_soft,
    )

    body    = request.get_json() or {}
    payload = normalise_manual_input(body)
    errors  = validate_payload(payload)
    if errors:
        return error("; ".join(errors), 400)

    firm_id = get_current_firm_id()

    best = _tier1_exact(payload, firm_id)
    if not best:
        best = _tier2_strong(payload, firm_id)
    if not best:
        # Try embedding if OpenAI is configured
        try:
            from routes.ai import generate_embedding
            embedding = generate_embedding(payload["full_name"])
            best = _tier3_soft(embedding, firm_id)
        except Exception:
            pass

    if not best:
        best = {"match_type": MatchType.none, "confidence_score": 0.0, "matched_record_id": None}

    matched = None
    if best.get("matched_record_id"):
        rec = ConflictIndex.query.get(best["matched_record_id"])
        if rec:
            matched = {
                "record_id":        rec.record_id,
                "full_name":        rec.full_name,
                "case_type":        rec.case_type,
                "passport_numbers": rec.passport_numbers or [],
            }

    return success(data={
        "match_type":       best["match_type"].value,
        "confidence_score": float(best["confidence_score"]),
        "matched_record":   matched,
    })
