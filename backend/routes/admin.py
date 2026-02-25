"""
admin.py — Admin and lawyer dashboard routes.

Page routes    → render_template (return HTML)
Data routes    → return JSON  (called by page JS via fetch)
"""

from datetime import date, datetime, timezone
from flask import Blueprint, request, render_template, session, redirect, url_for
from sqlalchemy import func, cast, Date

from database import db
from models import (
    Client, ClientStatus, ClientChannel,
    ConflictResult, MatchType, ConflictDecision,
    ConflictIndex, AuditLog, AIBrief,
    RequestedDocument, EngagementLetter, User,
    CalendlyBooking, KYCRecord,
)
from utils.response import success, error, not_found
from utils.auth import login_required, admin_required, get_current_firm_id, get_current_user

admin_bp = Blueprint("admin", __name__)


# ════════════════════════════════════════════════════════════
#  PAGE ROUTES  (render HTML templates)
# ════════════════════════════════════════════════════════════

@admin_bp.route("/")
@login_required
def dashboard():
    """Render the main admin dashboard."""
    return render_template("admin/index.html", now=datetime.now(timezone.utc))


@admin_bp.route("/clients")
@login_required
def clients_page():
    return render_template("admin/clients.html", now=datetime.now(timezone.utc))


@admin_bp.route("/clients/<client_id>")
@login_required
def case_detail_page(client_id):
    return render_template("admin/case_detail.html", client_id=client_id, now=datetime.now(timezone.utc))


@admin_bp.route("/conflict")
@login_required
def conflict_page():
    return render_template("admin/conflict.html", now=datetime.now(timezone.utc))


@admin_bp.route("/database")
@login_required
def database_page():
    return render_template("admin/database.html", now=datetime.now(timezone.utc))


@admin_bp.route("/settings")
@admin_required
def settings_page():
    return render_template("admin/settings.html", now=datetime.now(timezone.utc))


@admin_bp.route("/audit")
@admin_required
def audit_page():
    return render_template("admin/audit.html", now=datetime.now(timezone.utc))


@admin_bp.route("/briefs")
@login_required
def briefs_page():
    return render_template("admin/clients.html", filter="has_brief", now=datetime.now(timezone.utc))


@admin_bp.route("/letters")
@login_required
def letters_page():
    return render_template("admin/clients.html", filter="has_letter", now=datetime.now(timezone.utc))


@admin_bp.route("/documents")
@login_required
def documents_page():
    return render_template("admin/clients.html", filter="has_docs", now=datetime.now(timezone.utc))


# ════════════════════════════════════════════════════════════
#  DATA / API ROUTES  (return JSON, called by page JS)
# ════════════════════════════════════════════════════════════

# ── Dashboard stats ───────────────────────────────────────
@admin_bp.route("/stats")
@login_required
def stats():
    """
    GET /admin/stats
    Returns the four stat card values + channel breakdown.
    """
    firm_id = get_current_firm_id()
    today   = date.today()

    try:
        total_clients = Client.query.filter_by(firm_id=firm_id).count()

        pending_review = Client.query.filter(
            Client.firm_id == firm_id,
            Client.status.in_([
                ClientStatus.manual_review,
                ClientStatus.review,
            ])
        ).count()

        # Unresolved conflicts (match_type != none AND decision == pending)
        conflicts_found = (
            db.session.query(ConflictResult)
            .join(Client, ConflictResult.client_id == Client.client_id)
            .filter(
                Client.firm_id == firm_id,
                ConflictResult.match_type != MatchType.none,
                ConflictResult.decision   == ConflictDecision.pending,
            )
            .count()
        )

        # Approved today
        approved_today = Client.query.filter(
            Client.firm_id == firm_id,
            Client.status  == ClientStatus.approved,
            cast(Client.created_at, Date) == today,
        ).count()

        # Conflict queue (pending decisions only)
        conflicts_pending = (
            db.session.query(ConflictResult)
            .join(Client, ConflictResult.client_id == Client.client_id)
            .filter(
                Client.firm_id == firm_id,
                ConflictResult.match_type != MatchType.none,
                ConflictResult.decision   == ConflictDecision.pending,
            )
            .count()
        )

        # Channel breakdown
        channel_whatsapp = Client.query.filter_by(
            firm_id=firm_id, channel=ClientChannel.whatsapp
        ).count()
        channel_web = Client.query.filter_by(
            firm_id=firm_id, channel=ClientChannel.web
        ).count()

    except Exception as e:
        return error(f"Database error: {str(e)}", 500)

    return success(data={
        "total_clients":    total_clients,
        "pending_review":   pending_review,
        "conflicts_found":  conflicts_found,
        "approved_today":   approved_today,
        "conflicts_pending": conflicts_pending,
        "channel_whatsapp": channel_whatsapp,
        "channel_web":      channel_web,
    })


# ── Activity feed ─────────────────────────────────────────
@admin_bp.route("/activity")
@login_required
def activity():
    """
    GET /admin/activity?limit=10
    Returns recent audit log entries for the firm.
    """
    firm_id = get_current_firm_id()
    limit   = min(int(request.args.get("limit", 10)), 50)

    try:
        logs = (
            AuditLog.query
            .filter_by(firm_id=firm_id)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
            .all()
        )
    except Exception as e:
        return error(f"Database error: {str(e)}", 500)

    return success(data={
        "activity": [
            {
                "log_id":      log.log_id,
                "action":      log.action,
                "record_type": log.record_type,
                "record_id":   log.record_id,
                "timestamp":   log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ]
    })


# ── Clients table data ────────────────────────────────────
@admin_bp.route("/clients-data")
@login_required
def clients_data():
    """
    GET /admin/clients-data
    Query params: status, channel, search, page, per_page, limit
    Returns client rows for the table with latest conflict score.
    """
    firm_id  = get_current_firm_id()
    search   = request.args.get("search", "").strip()
    status   = request.args.get("status")
    channel  = request.args.get("channel")
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", 20)), 100)
    limit    = request.args.get("limit")

    try:
        q = Client.query.filter_by(firm_id=firm_id)

        if search:
            q = q.filter(
                db.or_(
                    Client.full_name.ilike(f"%{search}%"),
                    Client.reference_id.ilike(f"%{search}%"),
                    Client.email.ilike(f"%{search}%"),
                )
            )

        if status:
            try:
                q = q.filter(Client.status == ClientStatus[status])
            except KeyError:
                pass

        if channel:
            try:
                q = q.filter(Client.channel == ClientChannel[channel])
            except KeyError:
                pass

        q = q.order_by(Client.created_at.desc())

        if limit:
            clients = q.limit(int(limit)).all()
            total   = q.count()
        else:
            total   = q.count()
            clients = q.offset((page - 1) * per_page).limit(per_page).all()

        # Fetch latest conflict score per client in one query
        conflict_scores = {}
        if clients:
            client_ids = [c.client_id for c in clients]
            results = (
                db.session.query(
                    ConflictResult.client_id,
                    ConflictResult.confidence_score,
                    ConflictResult.match_type,
                )
                .filter(ConflictResult.client_id.in_(client_ids))
                .order_by(ConflictResult.confidence_score.desc())
                .all()
            )
            for r in results:
                if r.client_id not in conflict_scores:
                    conflict_scores[r.client_id] = {
                        "score":      float(r.confidence_score),
                        "match_type": r.match_type.value,
                    }

    except Exception as e:
        return error(f"Database error: {str(e)}", 500)

    return success(data={
        "clients": [
            {
                "client_id":      c.client_id,
                "reference_id":   c.reference_id,
                "full_name":      c.full_name,
                "email":          c.email,
                "phone":          c.phone,
                "channel":        c.channel.value,
                "status":         c.status.value,
                "conflict_score": conflict_scores.get(c.client_id, {}).get("score"),
                "match_type":     conflict_scores.get(c.client_id, {}).get("match_type"),
                "created_at":     c.created_at.isoformat() if c.created_at else None,
            }
            for c in clients
        ],
        "total": total,
        "page":  page if not limit else 1,
        "pages": max(1, -(-total // per_page)) if not limit else 1,
    })


# ── Conflict queue ────────────────────────────────────────
@admin_bp.route("/conflict-queue")
@login_required
def conflict_queue():
    """
    GET /admin/conflict-queue
    Returns pending conflict results with client info.
    """
    firm_id = get_current_firm_id()

    try:
        results = (
            db.session.query(ConflictResult, Client)
            .join(Client, ConflictResult.client_id == Client.client_id)
            .filter(
                Client.firm_id == firm_id,
                ConflictResult.match_type != MatchType.none,
                ConflictResult.decision   == ConflictDecision.pending,
            )
            .order_by(ConflictResult.confidence_score.desc())
            .limit(20)
            .all()
        )
    except Exception as e:
        return error(f"Database error: {str(e)}", 500)

    return success(data={
        "conflicts": [
            {
                "conflict_id":      cr.conflict_id,
                "client_id":        cl.client_id,
                "reference_id":     cl.reference_id,
                "full_name":        cl.full_name,
                "match_type":       cr.match_type.value,
                "confidence_score": float(cr.confidence_score),
            }
            for cr, cl in results
        ]
    })


# ── Single case detail data ───────────────────────────────
@admin_bp.route("/clients/<client_id>/data")
@login_required
def case_detail_data(client_id):
    """
    GET /admin/clients/<client_id>/data
    Returns full client record with all related data.
    """
    firm_id = get_current_firm_id()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    # Latest conflict result
    conflict = (
        ConflictResult.query
        .filter_by(client_id=client_id)
        .order_by(ConflictResult.confidence_score.desc())
        .first()
    )

    # Latest AI brief
    brief = (
        AIBrief.query
        .filter_by(client_id=client_id)
        .order_by(AIBrief.generated_at.desc())
        .first()
    )

    # Requested docs
    requested_docs = RequestedDocument.query.filter_by(client_id=client_id).all()

    # KYC record
    kyc = KYCRecord.query.filter_by(client_id=client_id).first()

    return success(data={
        "client": {
            "client_id":    client.client_id,
            "reference_id": client.reference_id,
            "full_name":    client.full_name,
            "email":        client.email,
            "phone":        client.phone,
            "channel":      client.channel.value,
            "status":       client.status.value,
            "created_at":   client.created_at.isoformat() if client.created_at else None,
        },
        "passports": [
            {
                "passport_id":     p.passport_id,
                "passport_number": p.passport_number,
                "nationality":     p.nationality,
                "date_of_birth":   p.date_of_birth,
                "expiry_date":     p.expiry_date,
            }
            for p in client.passports
        ],
        "emirates_ids": [
            {"id_record_id": e.id_record_id, "id_number": e.id_number}
            for e in client.emirates_ids
        ],
        "statements": [
            {
                "statement_id":      s.statement_id,
                "sequence_number":   s.sequence_number,
                "client_edited_text": s.client_edited_text,
                "channel":           s.channel.value,
            }
            for s in client.statements
        ],
        "documents": [
            {
                "document_id":    d.document_id,
                "saved_filename": d.saved_filename,
                "file_type":      d.file_type.value,
                "uploaded_at":    d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in client.documents
        ],
        "conflict_result": {
            "conflict_id":      conflict.conflict_id,
            "match_type":       conflict.match_type.value,
            "confidence_score": float(conflict.confidence_score),
            "decision":         conflict.decision.value,
            "decision_reason":  conflict.decision_reason,
        } if conflict else None,
        "ai_brief": {
            "brief_id":             brief.brief_id,
            "client_summary":       brief.client_summary,
            "situation_overview":   brief.situation_overview,
            "key_facts":            brief.key_facts,
            "documents_provided":   brief.documents_provided,
            "inconsistencies":      brief.inconsistencies,
            "questions_for_lawyer": brief.questions_for_lawyer,
            "risk_notes":           brief.risk_notes,
            "lawyer_notes":         brief.lawyer_notes,
            "generated_at":         brief.generated_at.isoformat() if brief.generated_at else None,
        } if brief else None,
        "requested_docs": [
            {
                "request_id":    rd.request_id,
                "document_type": rd.document_type.value,
                "notes":         rd.notes,
                "is_received":   rd.is_received,
            }
            for rd in requested_docs
        ],
        "kyc": {
            "kyc_id":               kyc.kyc_id,
            "occupation":           kyc.occupation,
            "employer":             kyc.employer,
            "country_of_residence": kyc.country_of_residence,
            "source_of_funds":      kyc.source_of_funds,
            "is_pep":               kyc.is_pep,
            "pep_details":          kyc.pep_details,
            "sanctions_ack":        kyc.sanctions_ack,
            "submitted_at":         kyc.submitted_at.isoformat() if kyc.submitted_at else None,
        } if kyc else None,
    })


# ── Update client status ──────────────────────────────────
@admin_bp.route("/clients/<client_id>/status", methods=["PUT"])
@login_required
def update_status(client_id):
    """
    PUT /admin/clients/<client_id>/status
    Body: { "status": str, "reason": str (optional) }
    """
    firm_id = get_current_firm_id()
    body    = request.get_json() or {}
    user    = get_current_user()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    new_status_str = body.get("status")
    try:
        new_status = ClientStatus[new_status_str]
    except (KeyError, TypeError):
        return error(f"Invalid status: {new_status_str}")

    old_status = client.status.value
    client.status = new_status
    _write_audit(firm_id, user["user_id"],
                 f"Client '{client.full_name}' status changed: {old_status} → {new_status_str}",
                 "client", client_id)
    db.session.commit()

    # Fire notification email for key status transitions
    if new_status_str in ("approved", "rejected", "context_collection") and client.email:
        try:
            from tasks.notifications import send_status_email
            send_status_email.delay(client_id, new_status_str)
        except Exception:
            pass  # Celery/SendGrid not available

    return success(data={
        "client_id":  client_id,
        "new_status": new_status_str,
    })


# ── Conflict decision ─────────────────────────────────────
@admin_bp.route("/conflict/<conflict_id>/decide", methods=["POST"])
@login_required
def decide_conflict(conflict_id):
    """
    POST /admin/conflict/<conflict_id>/decide
    Body: { "decision": "approved" | "rejected", "reason": str }
    """
    firm_id = get_current_firm_id()
    body    = request.get_json() or {}
    user    = get_current_user()

    decision = body.get("decision")
    reason   = body.get("reason", "").strip()

    if decision not in ("approved", "rejected"):
        return error("decision must be 'approved' or 'rejected'.")
    if decision == "approved" and not reason:
        return error("A reason is required when approving a conflict case.")

    # Verify the conflict belongs to this firm
    conflict = (
        db.session.query(ConflictResult)
        .join(Client, ConflictResult.client_id == Client.client_id)
        .filter(ConflictResult.conflict_id == conflict_id, Client.firm_id == firm_id)
        .first()
    )
    if not conflict:
        return not_found("Conflict result")

    conflict.decision        = ConflictDecision[decision]
    conflict.decision_reason = reason
    conflict.reviewed_by     = user["user_id"]
    conflict.decision_at     = datetime.now(timezone.utc)

    # Update client status accordingly
    client = Client.query.get(conflict.client_id)
    if client:
        client.status = ClientStatus.approved if decision == "approved" else ClientStatus.rejected

    _write_audit(firm_id, user["user_id"],
                 f"Conflict {conflict_id} {decision}. Reason: {reason or 'none'}",
                 "conflict", conflict_id)
    db.session.commit()

    return success(data={"conflict_id": conflict_id, "decision": decision})


# ── Conflict list ─────────────────────────────────────────
@admin_bp.route("/conflict-list")
@login_required
def conflict_list():
    """GET /admin/conflict-list — all conflict results for the firm."""
    firm_id    = get_current_firm_id()
    filter_by  = request.args.get("filter")  # "pending" | "all"
    page       = max(1, int(request.args.get("page", 1)))
    per_page   = min(int(request.args.get("per_page", 20)), 100)

    q = (
        db.session.query(ConflictResult, Client)
        .join(Client, ConflictResult.client_id == Client.client_id)
        .filter(Client.firm_id == firm_id)
    )

    if filter_by == "pending":
        q = q.filter(ConflictResult.decision == ConflictDecision.pending)

    total   = q.count()
    results = q.order_by(ConflictResult.confidence_score.desc()).offset((page-1)*per_page).limit(per_page).all()

    return success(data={
        "conflicts": [
            {
                "conflict_id":      cr.conflict_id,
                "client_id":        cl.client_id,
                "reference_id":     cl.reference_id,
                "full_name":        cl.full_name,
                "match_type":       cr.match_type.value,
                "confidence_score": float(cr.confidence_score),
                "decision":         cr.decision.value,
                "created_at":       cr.created_at.isoformat() if cr.created_at else None,
            }
            for cr, cl in results
        ],
        "total": total, "page": page, "pages": max(1, -(-total // per_page)),
    })


# ── Firm database (conflict index) ────────────────────────
@admin_bp.route("/database-data")
@login_required
def database_data():
    """GET /admin/database-data — list conflict_index records."""
    firm_id  = get_current_firm_id()
    search   = request.args.get("search", "").strip()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(int(request.args.get("per_page", 20)), 100)

    q = ConflictIndex.query.filter_by(firm_id=firm_id)
    if search:
        q = q.filter(ConflictIndex.full_name.ilike(f"%{search}%"))

    total   = q.count()
    records = q.order_by(ConflictIndex.full_name).offset((page-1)*per_page).limit(per_page).all()

    return success(data={
        "records": [
            {
                "record_id":        r.record_id,
                "full_name":        r.full_name,
                "nationality":      r.nationality,
                "passport_numbers": r.passport_numbers,
                "case_type":        r.case_type,
                "source_file":      r.source_file,
            }
            for r in records
        ],
        "total": total, "page": page, "pages": max(1, -(-total // per_page)),
    })


# ── Firm-requested document checklist ────────────────────
@admin_bp.route("/clients/<client_id>/request-document", methods=["POST"])
@login_required
def request_document(client_id):
    """
    POST /admin/clients/<client_id>/request-document
    Body: { "document_type": str, "notes": str (optional) }
    """
    import uuid as _uuid
    from models import DocumentCategory

    firm_id = get_current_firm_id()
    body    = request.get_json() or {}

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    doc_type_str = body.get("document_type")
    try:
        doc_type = DocumentCategory[doc_type_str.lower().replace(" ", "_")]
    except (KeyError, AttributeError):
        return error(f"Invalid document type: {doc_type_str}")

    req_doc = RequestedDocument(
        request_id=str(_uuid.uuid4()),
        client_id=client_id,
        firm_id=firm_id,
        document_type=doc_type,
        notes=body.get("notes", ""),
    )
    db.session.add(req_doc)
    db.session.commit()

    return success(data={"request_id": req_doc.request_id}, message="Document requested.", status_code=201)


# ── Cancel a document request ─────────────────────────────
@admin_bp.route("/clients/<client_id>/request-document/<request_id>", methods=["DELETE"])
@login_required
def cancel_document_request(client_id, request_id):
    """DELETE /admin/clients/<client_id>/request-document/<request_id>"""
    firm_id = get_current_firm_id()
    client  = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")
    req = RequestedDocument.query.filter_by(request_id=request_id, client_id=client_id).first()
    if not req:
        return not_found("Document request")
    db.session.delete(req)
    db.session.commit()
    return success(data={"request_id": request_id, "deleted": True})


# ── Firm-wide pending document requests ───────────────────
@admin_bp.route("/document-requests")
@login_required
def document_requests():
    """
    GET /admin/document-requests?pending_only=true
    Returns all firm-requested documents across all clients.
    """
    firm_id      = get_current_firm_id()
    pending_only = request.args.get("pending_only", "true").lower() == "true"

    query = RequestedDocument.query.filter_by(firm_id=firm_id)
    if pending_only:
        query = query.filter_by(is_received=False)
    requests_list = query.order_by(RequestedDocument.created_at.desc()).limit(200).all()

    # Attach client name
    client_cache = {}
    result = []
    for rd in requests_list:
        if rd.client_id not in client_cache:
            c = Client.query.get(rd.client_id)
            client_cache[rd.client_id] = c
        c = client_cache.get(rd.client_id)
        result.append({
            "request_id":    rd.request_id,
            "client_id":     rd.client_id,
            "client_name":   c.full_name if c else "Unknown",
            "reference_id":  c.reference_id if c else "",
            "document_type": rd.document_type.value,
            "notes":         rd.notes,
            "is_received":   rd.is_received,
            "created_at":    rd.created_at.isoformat(),
        })

    return success(data={"requests": result, "total": len(result)})


# ── Engagement letter ─────────────────────────────────────
@admin_bp.route("/clients/<client_id>/engagement-letter", methods=["POST"])
@login_required
def create_engagement_letter(client_id):
    """
    POST /admin/clients/<client_id>/engagement-letter
    Body: {
      matter_type, scope_of_work, fee_structure,
      retainer_amount, billing_type, timeline
    }
    Creates (or replaces) the engagement letter and generates the PDF.
    """
    import uuid as _uuid
    from flask import current_app
    from utils.pdf import generate_engagement_letter

    firm_id = get_current_firm_id()
    body    = request.get_json() or {}
    user    = get_current_user()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    # Retire any existing letter for this client
    existing = (
        EngagementLetter.query
        .filter_by(client_id=client_id)
        .order_by(EngagementLetter.created_at.desc())
        .first()
    )

    retainer_raw = body.get("retainer_amount")
    try:
        retainer = float(retainer_raw) if retainer_raw not in (None, "", "null") else None
    except (TypeError, ValueError):
        retainer = None

    if existing:
        letter = existing
        letter.matter_type     = body.get("matter_type", "")
        letter.scope_of_work   = body.get("scope_of_work", "")
        letter.fee_structure   = body.get("fee_structure", "")
        letter.retainer_amount = retainer
        letter.billing_type    = body.get("billing_type", "")
        letter.timeline        = body.get("timeline", "")
    else:
        letter = EngagementLetter(
            letter_id      = str(_uuid.uuid4()),
            client_id      = client_id,
            matter_type    = body.get("matter_type", ""),
            scope_of_work  = body.get("scope_of_work", ""),
            fee_structure  = body.get("fee_structure", ""),
            retainer_amount= retainer,
            billing_type   = body.get("billing_type", ""),
            timeline       = body.get("timeline", ""),
        )
        db.session.add(letter)
        db.session.flush()  # get letter_id

    # Generate PDF
    try:
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        firm = db.session.query(__import__("models").LawFirm).get(firm_id)
        rel_path = generate_engagement_letter(letter, client, firm, upload_folder)
        letter.pdf_path = rel_path
    except Exception as e:
        current_app.logger.error(f"PDF generation failed: {e}")
        return error(f"PDF generation failed: {str(e)}", 500)

    _write_audit(firm_id, user["user_id"],
                 f"Engagement letter generated for '{client.full_name}'",
                 "engagement_letter", letter.letter_id)
    db.session.commit()

    return success(
        data={
            "letter_id": letter.letter_id,
            "pdf_path":  letter.pdf_path,
        },
        message="Engagement letter generated.",
        status_code=201,
    )


@admin_bp.route("/clients/<client_id>/engagement-letter", methods=["GET"])
@login_required
def get_engagement_letter(client_id):
    """GET /admin/clients/<client_id>/engagement-letter — letter details + PDF status."""
    firm_id = get_current_firm_id()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    letter = (
        EngagementLetter.query
        .filter_by(client_id=client_id)
        .order_by(EngagementLetter.created_at.desc())
        .first()
    )
    if not letter:
        return success(data={"letter": None})

    return success(data={
        "letter": {
            "letter_id":       letter.letter_id,
            "matter_type":     letter.matter_type,
            "scope_of_work":   letter.scope_of_work,
            "fee_structure":   letter.fee_structure,
            "retainer_amount": float(letter.retainer_amount) if letter.retainer_amount else None,
            "billing_type":    letter.billing_type,
            "timeline":        letter.timeline,
            "pdf_path":        letter.pdf_path,
            "docuseal_status": letter.docuseal_status.value if letter.docuseal_status else None,
            "signed_at":       letter.signed_at.isoformat() if letter.signed_at else None,
            "created_at":      letter.created_at.isoformat() if letter.created_at else None,
            "has_pdf":         bool(letter.pdf_path),
        }
    })


@admin_bp.route("/clients/<client_id>/engagement-letter/download")
@login_required
def download_engagement_letter(client_id):
    """GET /admin/clients/<client_id>/engagement-letter/download — serve the PDF."""
    import os
    from flask import current_app, send_file

    firm_id = get_current_firm_id()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    letter = (
        EngagementLetter.query
        .filter_by(client_id=client_id)
        .order_by(EngagementLetter.created_at.desc())
        .first()
    )
    if not letter or not letter.pdf_path:
        return not_found("Engagement letter PDF")

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    abs_path = os.path.join(upload_folder, letter.pdf_path)
    if not os.path.isfile(abs_path):
        return not_found("PDF file on disk")

    safe_name = f"EngagementLetter_{client.reference_id}.pdf"
    return send_file(abs_path, as_attachment=True, download_name=safe_name, mimetype="application/pdf")


# ── DocuSeal: send engagement letter for signature ────────
@admin_bp.route("/clients/<client_id>/engagement-letter/send", methods=["POST"])
@login_required
def send_letter_for_signature(client_id):
    """
    POST /admin/clients/<client_id>/engagement-letter/send
    Uploads the PDF to DocuSeal, creates a submission and sends the
    signing link to the client's email address.
    """
    import os, requests as http
    from flask import current_app

    firm_id = get_current_firm_id()
    user    = get_current_user()

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")
    if not client.email:
        return error("Client has no email address — cannot send for signature.")

    letter = (
        EngagementLetter.query
        .filter_by(client_id=client_id)
        .order_by(EngagementLetter.created_at.desc())
        .first()
    )
    if not letter or not letter.pdf_path:
        return error("Generate the engagement letter PDF first.")

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    abs_path      = os.path.join(upload_folder, letter.pdf_path)
    if not os.path.isfile(abs_path):
        return error("PDF file not found on disk. Please regenerate.")

    docuseal_url = current_app.config.get("DOCUSEAL_URL", "").rstrip("/")
    docuseal_key = current_app.config.get("DOCUSEAL_API_KEY", "")

    if not docuseal_url or not docuseal_key:
        return error("DocuSeal is not configured (DOCUSEAL_URL / DOCUSEAL_API_KEY missing).", 503)

    headers = {"X-Auth-Token": docuseal_key}

    # Step 1: Upload PDF as a template
    try:
        with open(abs_path, "rb") as f:
            upload_resp = http.post(
                f"{docuseal_url}/api/templates/pdf",
                headers=headers,
                files={"file": (f"EngagementLetter_{client.reference_id}.pdf", f, "application/pdf")},
                timeout=30,
            )
        upload_resp.raise_for_status()
        template_id = upload_resp.json().get("id")
        if not template_id:
            raise ValueError("DocuSeal template upload returned no ID.")
    except Exception as e:
        current_app.logger.error(f"DocuSeal upload failed: {e}")
        return error(f"DocuSeal upload failed: {str(e)}", 502)

    # Step 2: Create a submission (sends email to client)
    firm = db.session.query(__import__("models").LawFirm).get(firm_id)
    submission_payload = {
        "template_id": template_id,
        "send_email":  True,
        "submitters": [
            {
                "role":  "Client",
                "email": client.email,
                "name":  client.full_name,
            }
        ],
        "message": {
            "subject": f"Engagement Letter — {firm.firm_name}",
            "body": (
                f"Dear {client.full_name},\n\n"
                f"Please review and sign your engagement letter with {firm.firm_name}.\n\n"
                "Click the button below to sign electronically.\n\nBest regards,\n"
                f"{firm.firm_name}"
            ),
        },
    }
    try:
        sub_resp = http.post(
            f"{docuseal_url}/api/submissions",
            headers={**headers, "Content-Type": "application/json"},
            json=submission_payload,
            timeout=30,
        )
        sub_resp.raise_for_status()
        submission_id = sub_resp.json()[0].get("id") if sub_resp.json() else None
    except Exception as e:
        current_app.logger.error(f"DocuSeal submission failed: {e}")
        return error(f"DocuSeal submission failed: {str(e)}", 502)

    # Update EngagementLetter record
    letter.docuseal_status      = __import__("models").DocuSealStatus.sent
    letter.docuseal_document_id = str(submission_id) if submission_id else None
    _write_audit(firm_id, user["user_id"],
                 f"Engagement letter sent for signature to {client.email}",
                 "engagement_letter", letter.letter_id)
    db.session.commit()

    return success(
        data={"submission_id": submission_id, "docuseal_status": "sent"},
        message=f"Engagement letter sent to {client.email} for signing.",
    )


# ── DocuSeal webhook (called by DocuSeal when document is signed) ─
@admin_bp.route("/webhooks/docuseal", methods=["POST"])
def docuseal_webhook():
    """
    POST /admin/webhooks/docuseal
    DocuSeal posts a JSON event when a submission is completed.
    No auth required (DocuSeal doesn't support custom auth headers in
    the community edition), but we verify the document ID matches a
    record in our DB.
    """
    from flask import current_app
    data = request.get_json(silent=True) or {}

    event_type = data.get("event_type", "")
    if event_type not in ("form.completed", "submission.completed"):
        return success(message="Event ignored.")

    submission_id = str(data.get("data", {}).get("id", ""))
    if not submission_id:
        return success(message="No submission ID.")

    letter = EngagementLetter.query.filter_by(
        docuseal_document_id=submission_id
    ).first()
    if not letter:
        current_app.logger.warning(f"DocuSeal webhook: no letter for submission {submission_id}")
        return success(message="Unknown submission.")

    letter.docuseal_status = __import__("models").DocuSealStatus.signed
    letter.signed_at       = datetime.now(timezone.utc)

    # Write audit
    client = Client.query.get(letter.client_id)
    if client:
        _write_audit(
            client.firm_id, None,
            f"Engagement letter signed by {client.full_name} via DocuSeal",
            "engagement_letter", letter.letter_id,
        )

    db.session.commit()
    current_app.logger.info(f"DocuSeal: letter {letter.letter_id} marked signed.")
    return success(message="Signed.")


# ── Generate AI analysis (synchronous, no Celery required) ───────────────────
@admin_bp.route("/clients/<client_id>/analysis/generate", methods=["POST"])
@login_required
def generate_analysis(client_id):
    """
    POST /admin/clients/<client_id>/analysis/generate
    Runs Claude analysis synchronously and saves result to ai_briefs table.
    """
    import uuid as _uuid
    from flask import current_app
    from models import ConflictResult

    firm_id = get_current_firm_id()
    client  = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    if not client.statements:
        return error("Client has no statements yet. Analysis requires at least one statement.")

    latest_conflict = (
        ConflictResult.query
        .filter_by(client_id=client_id)
        .order_by(ConflictResult.created_at.desc())
        .first()
    )

    client_data = {
        "full_name": client.full_name,
        "conflict_score": float(latest_conflict.confidence_score) if latest_conflict else 0.0,
        "statements": [
            {
                "sequence_number":    s.sequence_number,
                "client_edited_text": s.client_edited_text or "",
            }
            for s in sorted(client.statements, key=lambda x: x.sequence_number)
            if s.client_edited_text
        ],
        "documents": [
            {"file_type": d.file_type.value, "saved_filename": d.saved_filename}
            for d in client.documents
        ],
        "passports": [
            {"nationality": p.nationality}
            for p in client.passports if p.nationality
        ],
    }

    try:
        from routes.ai import generate_brief
        result = generate_brief(client_data)
    except RuntimeError as exc:
        return error(str(exc), 500)

    brief = AIBrief(
        brief_id              = str(_uuid.uuid4()),
        client_id             = client_id,
        client_summary        = result.get("client_summary"),
        situation_overview    = result.get("situation_overview"),
        key_facts             = result.get("key_facts"),
        documents_provided    = result.get("documents_provided"),
        inconsistencies       = result.get("inconsistencies"),
        questions_for_lawyer  = result.get("questions_for_lawyer"),
        risk_notes            = result.get("risk_notes"),
        raw_gpt_response      = result.get("raw_gpt_response"),
    )
    db.session.add(brief)
    db.session.commit()

    return success(data={"brief_id": brief.brief_id}, message="Analysis generated.")


# ── AI brief notes ────────────────────────────────────────
@admin_bp.route("/clients/<client_id>/brief/notes", methods=["PUT"])
@login_required
def update_brief_notes(client_id):
    """PUT /admin/clients/<client_id>/brief/notes — save lawyer annotations."""
    firm_id = get_current_firm_id()
    body    = request.get_json() or {}

    client = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    brief = AIBrief.query.filter_by(client_id=client_id).order_by(AIBrief.generated_at.desc()).first()
    if not brief:
        return not_found("AI Brief")

    brief.lawyer_notes = body.get("notes") or body.get("lawyer_notes", "")
    db.session.commit()
    return success(message="Notes saved.")


# ── Settings ──────────────────────────────────────────────
@admin_bp.route("/settings-data")
@admin_required
def settings_data():
    """GET /admin/settings-data — return current firm settings."""
    firm_id = get_current_firm_id()
    firm    = db.session.query(
        __import__('models').LawFirm
    ).get(firm_id)

    return success(data={
        "firm_name":                     firm.firm_name if firm else "",
        "auto_approve_clear_conflicts":  False,
    })


# ── Audit log data ────────────────────────────────────────
@admin_bp.route("/audit-data")
@admin_required
def audit_log():
    """GET /admin/audit-data — full audit log for the firm."""
    firm_id     = get_current_firm_id()
    page        = max(1, int(request.args.get("page", 1)))
    per_page    = min(int(request.args.get("per_page", 50)), 100)
    search      = request.args.get("search", "").strip()
    record_type = request.args.get("record_type", "").strip()

    q = AuditLog.query.filter_by(firm_id=firm_id)
    if search:
        q = q.filter(AuditLog.action.ilike(f"%{search}%"))
    if record_type:
        q = q.filter(AuditLog.record_type == record_type)

    total = q.count()
    logs  = (
        q.order_by(AuditLog.timestamp.desc())
        .offset((page-1)*per_page).limit(per_page).all()
    )

    return success(data={
        "logs": [
            {
                "log_id":      l.log_id,
                "action":      l.action,
                "performed_by": l.performed_by,
                "record_type": l.record_type,
                "record_id":   l.record_id,
                "timestamp":   l.timestamp.isoformat() if l.timestamp else None,
            }
            for l in logs
        ],
        "total": total,
    })


# ── Calendly: get booking link ────────────────────────────
@admin_bp.route("/clients/<client_id>/calendly-link")
@login_required
def calendly_link(client_id):
    """
    GET /admin/clients/<client_id>/calendly-link
    Returns the pre-filled Calendly scheduling URL for this client.
    """
    from urllib.parse import urlencode
    from flask import current_app

    firm_id = get_current_firm_id()
    client  = Client.query.filter_by(client_id=client_id, firm_id=firm_id).first()
    if not client:
        return not_found("Client")

    base_link = current_app.config.get("CALENDLY_LINK", "").strip()
    if not base_link:
        return error("CALENDLY_LINK is not configured.", 503)

    params = {}
    if client.full_name:
        params["name"] = client.full_name
    if client.email:
        params["email"] = client.email

    full_url = base_link + ("?" + urlencode(params) if params else "")

    # Fetch upcoming bookings for this client
    bookings = (
        CalendlyBooking.query
        .filter_by(client_id=client_id, status="active")
        .order_by(CalendlyBooking.start_time.desc())
        .limit(5).all()
    )

    return success(data={
        "calendly_url": full_url,
        "bookings": [
            {
                "booking_id":    b.booking_id,
                "event_name":    b.event_name,
                "start_time":    b.start_time.isoformat() if b.start_time else None,
                "end_time":      b.end_time.isoformat() if b.end_time else None,
                "cancel_url":    b.cancel_url,
                "reschedule_url":b.reschedule_url,
                "status":        b.status,
            }
            for b in bookings
        ],
    })


# ── Calendly webhook ──────────────────────────────────────
@admin_bp.route("/webhooks/calendly", methods=["POST"])
def calendly_webhook():
    """
    POST /admin/webhooks/calendly
    Calendly posts events here when bookings are created/cancelled.
    Verify using HMAC-SHA256 signature if CALENDLY_WEBHOOK_SECRET is set.
    """
    import hashlib, hmac, uuid as _uuid
    from flask import current_app

    # Optional signature verification
    secret = current_app.config.get("CALENDLY_WEBHOOK_SECRET", "")
    if secret:
        sig_header = request.headers.get("Calendly-Webhook-Signature", "")
        body_bytes  = request.get_data()
        expected    = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig_header.split("=")[-1]):
            return error("Invalid webhook signature.", 401)

    payload    = request.get_json(silent=True) or {}
    event_type = payload.get("event", "")
    data       = payload.get("payload", {})

    if event_type not in ("invitee.created", "invitee.canceled"):
        return success(message="Event ignored.")

    # Extract invitee details
    invitee       = data.get("invitee", {}) or data
    scheduled_event= data.get("scheduled_event", {}) or {}

    invitee_email = invitee.get("email", "")
    invitee_name  = invitee.get("name", "")
    event_name    = scheduled_event.get("name", "")
    event_uuid    = scheduled_event.get("uri", "").split("/")[-1]
    cancel_url    = invitee.get("cancel_url", "")
    reschedule_url= invitee.get("reschedule_url", "")

    start_str = scheduled_event.get("start_time", "")
    end_str   = scheduled_event.get("end_time", "")
    try:
        from datetime import datetime as _dt
        start_time = _dt.fromisoformat(start_str.replace("Z", "+00:00")) if start_str else None
        end_time   = _dt.fromisoformat(end_str.replace("Z", "+00:00"))   if end_str   else None
    except Exception:
        start_time = end_time = None

    # Match to a client by email
    client = None
    firm   = None
    if invitee_email:
        client = Client.query.filter_by(email=invitee_email).first()
    if client:
        firm_id = client.firm_id
    else:
        # Can't match to a client — still log under the first firm (best-effort)
        from models import LawFirm
        first_firm = LawFirm.query.first()
        firm_id    = first_firm.firm_id if first_firm else None

    if not firm_id:
        return success(message="Could not determine firm.")

    if event_type == "invitee.created":
        # Upsert booking record
        existing = CalendlyBooking.query.filter_by(event_uuid=event_uuid).first() if event_uuid else None
        if not existing:
            booking = CalendlyBooking(
                booking_id    = str(_uuid.uuid4()),
                client_id     = client.client_id if client else None,
                firm_id       = firm_id,
                event_uuid    = event_uuid or None,
                event_name    = event_name,
                invitee_name  = invitee_name,
                invitee_email = invitee_email,
                start_time    = start_time,
                end_time      = end_time,
                cancel_url    = cancel_url,
                reschedule_url= reschedule_url,
                status        = "active",
                raw_payload   = payload,
            )
            db.session.add(booking)

        _write_audit(
            firm_id, None,
            f"Calendly booking: {invitee_name} <{invitee_email}> scheduled '{event_name}'"
            + (f" for {start_time.strftime('%d %b %Y %H:%M')} UTC" if start_time else ""),
            "calendly_booking",
            client.client_id if client else None,
        )

    elif event_type == "invitee.canceled":
        booking = CalendlyBooking.query.filter_by(event_uuid=event_uuid).first() if event_uuid else None
        if booking:
            booking.status = "canceled"
        _write_audit(
            firm_id, None,
            f"Calendly booking CANCELED: {invitee_name} <{invitee_email}> — '{event_name}'",
            "calendly_booking",
            client.client_id if client else None,
        )

    db.session.commit()
    return success(message="Booking recorded.")


# ════════════════════════════════════════════════════════════
#  Private helpers
# ════════════════════════════════════════════════════════════

def _write_audit(firm_id, performed_by, action, record_type=None, record_id=None):
    import uuid as _uuid
    from flask import current_app
    try:
        log = AuditLog(
            log_id=str(_uuid.uuid4()),
            firm_id=firm_id,
            action=action,
            performed_by=performed_by,
            record_type=record_type,
            record_id=record_id,
        )
        db.session.add(log)
        db.session.flush()
    except Exception as e:
        current_app.logger.warning(f"Audit log failed: {e}")
