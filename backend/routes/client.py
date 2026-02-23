"""
client.py — Client web portal routes (4-step intake wizard).

Page routes  → render_template (serve HTML)
Action routes → return JSON (called by page JS)

Portal links are pre-authenticated via ?token=<portal_token>.
Format: /client/<reference_id>?token=<portal_token>
"""

import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, render_template, redirect, url_for, g, current_app

from database import db
from models import (
    Client, ClientStatus, ClientChannel, WhatsAppState,
    Passport, EmiratesID, Statement, StatementChannel,
    Document, DocumentCategory, RequestedDocument, AuditLog
)
from utils.response import success, error, not_found, unauthorized
from utils.auth import client_token_auth, get_current_firm_id
from utils.reference import generate_reference_id, generate_portal_token, token_expiry
from utils.naming import make_document_filename

client_bp = Blueprint("client", __name__)


# ════════════════════════════════════════════════════════════
#  PAGE ROUTES  (render HTML templates)
# ════════════════════════════════════════════════════════════

@client_bp.route("/login", methods=["GET"])
def login_page():
    """Returning client — retrieve portal link by reference_id + email."""
    return render_template("client/login.html")


@client_bp.route("/request-link", methods=["POST"])
def request_link():
    """
    POST /client/request-link
    Body: { "reference_id": str, "email": str }

    Looks up the client by reference_id + email, returns portal link.
    Also queues an email send if SendGrid is configured (Step 25).
    """
    body         = request.get_json() or {}
    reference_id = (body.get("reference_id") or "").strip().upper()
    email        = (body.get("email") or "").strip().lower()

    if not reference_id or not email:
        return error("reference_id and email are required.", 400)

    client = Client.query.filter_by(reference_id=reference_id, email=email).first()
    if not client:
        return error("No matching record found. Please check your reference number and email.", 404)

    # Renew token if expired
    from datetime import datetime, timezone
    if client.token_expires_at and client.token_expires_at < datetime.now(timezone.utc):
        client.portal_token    = generate_portal_token()
        client.token_expires_at = token_expiry(30)
        db.session.commit()

    portal_link  = f"/client/{client.reference_id}?token={client.portal_token}"
    email_sent   = False

    # Queue portal link email via SendGrid
    try:
        from tasks.notifications import send_portal_link_email
        send_portal_link_email.delay(client.client_id, portal_link)
        email_sent = True
    except Exception:
        pass  # Celery/SendGrid not configured — degrade gracefully

    return success(data={
        "portal_link": portal_link,
        "email_sent":  email_sent,
    })


@client_bp.route("/start", methods=["GET"])
def start_page():
    """Step 1 — contact info form (no auth needed, fresh start)."""
    return render_template("client/start.html")


@client_bp.route("/<reference_id>", methods=["GET"])
@client_token_auth
def portal_entry(reference_id):
    """
    GET /client/<reference_id>?token=<token>
    Deep-links the client directly to their current step.
    The token has already been validated and g.client is set.
    """
    client = g.client

    # Verify the reference_id in the URL matches the token's client
    if client.reference_id != reference_id:
        return unauthorized("Invalid portal link.")

    step_map = {
        ClientStatus.pending:            "upload",
        ClientStatus.id_uploaded:        "upload",
        ClientStatus.conflict_check:     "statement",
        ClientStatus.manual_review:      "statement",
        ClientStatus.context_collection: "statement",
        ClientStatus.review:             "documents",
        ClientStatus.approved:           "confirmation",
        ClientStatus.rejected:           "confirmation",
    }

    step = step_map.get(client.status, "upload")
    token = request.args.get("token", "")

    # If in context_collection and KYC not yet submitted, go to KYC page first
    if client.status == ClientStatus.context_collection:
        from models import KYCRecord
        kyc_done = KYCRecord.query.filter_by(client_id=client.client_id).first()
        if not kyc_done:
            return redirect(url_for("client.kyc_page", reference_id=reference_id, token=token))

    return redirect(url_for(
        f"client.{step}_page",
        reference_id=reference_id,
        token=token
    ))


@client_bp.route("/<reference_id>/upload", methods=["GET"])
@client_token_auth
def upload_page(reference_id):
    """Step 2 — passport and ID upload."""
    client = g.client
    return render_template(
        "client/upload.html",
        client=client,
        token=request.args.get("token", ""),
    )


@client_bp.route("/<reference_id>/statement", methods=["GET"])
@client_token_auth
def statement_page(reference_id):
    """Step 3 — statement (voice or text)."""
    client = g.client
    return render_template(
        "client/statement.html",
        client=client,
        token=request.args.get("token", ""),
        existing_statements=client.statements,
    )


@client_bp.route("/<reference_id>/documents", methods=["GET"])
@client_token_auth
def documents_page(reference_id):
    """Step 4 — supporting documents."""
    client = g.client
    requested = RequestedDocument.query.filter_by(client_id=client.client_id).all()
    return render_template(
        "client/documents.html",
        client=client,
        token=request.args.get("token", ""),
        requested_docs=requested,
        uploaded_docs=client.documents,
    )


@client_bp.route("/<reference_id>/kyc", methods=["GET"])
@client_token_auth
def kyc_page(reference_id):
    """KYC questionnaire — shown after conflict clear, before statement."""
    client = g.client
    from models import KYCRecord
    existing_kyc = KYCRecord.query.filter_by(client_id=client.client_id).first()
    return render_template(
        "client/kyc.html",
        client=client,
        token=request.args.get("token", ""),
        existing_kyc=existing_kyc,
    )


@client_bp.route("/kyc/submit", methods=["POST"])
@client_token_auth
def kyc_submit():
    """
    POST /client/kyc/submit
    Body: {
      source_of_funds, is_pep (bool), pep_details,
      sanctions_ack (bool), occupation, employer, country_of_residence
    }
    """
    from models import KYCRecord
    client = g.client
    body   = request.get_json() or {}

    if not body.get("sanctions_ack"):
        return error("You must acknowledge the sanctions check to proceed.")

    # Upsert KYC record
    existing = KYCRecord.query.filter_by(client_id=client.client_id).first()
    if existing:
        kyc = existing
    else:
        import uuid as _uuid
        kyc = KYCRecord(kyc_id=str(_uuid.uuid4()), client_id=client.client_id)
        db.session.add(kyc)

    kyc.source_of_funds      = (body.get("source_of_funds") or "").strip() or None
    kyc.is_pep               = bool(body.get("is_pep"))
    kyc.pep_details          = (body.get("pep_details") or "").strip() or None
    kyc.sanctions_ack        = True
    kyc.occupation           = (body.get("occupation") or "").strip() or None
    kyc.employer             = (body.get("employer") or "").strip() or None
    kyc.country_of_residence = (body.get("country_of_residence") or "").strip() or None

    _write_audit(
        client.firm_id,
        f"KYC questionnaire submitted by client '{client.full_name}' ({client.reference_id})."
        + (" PEP declared." if kyc.is_pep else ""),
        "kyc", client.client_id,
    )
    db.session.commit()

    token = request.args.get("token", "")
    return success(
        data={
            "redirect": url_for(
                "client.statement_page",
                reference_id=client.reference_id,
                token=token,
            )
        },
        message="KYC submitted.",
    )


@client_bp.route("/<reference_id>/edit", methods=["GET"])
@client_token_auth
def edit_page(reference_id):
    """Edit profile page."""
    client = g.client
    return render_template(
        "client/edit.html",
        client=client,
        token=request.args.get("token", ""),
    )


@client_bp.route("/<reference_id>/confirmation", methods=["GET"])
@client_token_auth
def confirmation_page(reference_id):
    """Confirmation / thank you screen."""
    client = g.client
    return render_template(
        "client/confirmation.html",
        client=client,
        token=request.args.get("token", ""),
    )


# ════════════════════════════════════════════════════════════
#  STEP 1 — Create client record
# ════════════════════════════════════════════════════════════

@client_bp.route("/start", methods=["POST"])
def start_post():
    """
    POST /client/start
    Body: { "full_name", "email", "phone", "channel", "firm_id" (optional) }

    Creates client record, generates reference_id and portal_token.
    Queues initial WhatsApp message if channel == whatsapp (Step 13).

    Response:
    {
        "client": {
            "reference_id": "ITF-2026-00001",
            "portal_token": "...",
            "portal_link":  "/client/ITF-2026-00001?token=..."
        }
    }
    """
    body = request.get_json() or {}

    full_name = (body.get("full_name") or "").strip()
    email     = (body.get("email")     or "").strip().lower()
    phone     = (body.get("phone")     or "").strip()
    channel   = (body.get("channel")   or "web").strip().lower()

    # Basic validation
    errors = []
    if not full_name:           errors.append("full_name is required.")
    if not email:               errors.append("email is required.")
    if "@" not in email:        errors.append("email is invalid.")
    if not phone:               errors.append("phone is required.")
    if channel not in ("web", "whatsapp"): channel = "web"
    if errors:
        return error("; ".join(errors), 400)

    # Determine firm_id
    # When triggered by admin (New Intake modal), firm_id comes from session.
    # When a client self-registers via the public start page, we need a default firm.
    from flask import session as flask_session
    firm_id = (
        body.get("firm_id")
        or flask_session.get("firm_id")
        or _get_default_firm_id()
    )
    if not firm_id:
        return error("No firm configured. Contact your administrator.", 500)

    # Generate IDs
    reference_id  = generate_reference_id(firm_id)
    portal_token  = generate_portal_token()
    expires_at    = token_expiry(days=current_app.config.get("TOKEN_EXPIRY_DAYS", 30))

    client = Client(
        client_id      = str(uuid.uuid4()),
        firm_id        = firm_id,
        reference_id   = reference_id,
        portal_token   = portal_token,
        full_name      = full_name,
        email          = email,
        phone          = phone,
        channel        = ClientChannel[channel],
        whatsapp_state = WhatsAppState.greeting if channel == "whatsapp" else None,
        status         = ClientStatus.pending,
        token_expires_at = expires_at,
    )

    db.session.add(client)
    _write_audit(firm_id, f"New client created: {full_name} ({reference_id}) via {channel}.",
                 "client", client.client_id)
    db.session.commit()

    portal_link = f"/client/{reference_id}?token={portal_token}"

    # Queue WhatsApp greeting (Step 13 will implement the actual send)
    if channel == "whatsapp":
        _queue_whatsapp_greeting(client, portal_link)

    return success(
        data={
            "client": {
                "client_id":    client.client_id,
                "reference_id": reference_id,
                "portal_token": portal_token,
                "portal_link":  portal_link,
            }
        },
        message="Client created.",
        status_code=201,
    )


# ════════════════════════════════════════════════════════════
#  STEP 2 — Passport / ID upload (action routes)
# ════════════════════════════════════════════════════════════

@client_bp.route("/upload/passport", methods=["POST"])
@client_token_auth
def upload_passport():
    """
    POST /client/upload/passport
    Form: file, (optional) token in query string already validated

    Saves the image, creates a Passport record.
    OCR processing is a background task (Step 11).
    Triggers conflict check once OCR is done (Step 12).

    Response: { "passport_id", "preview_url", "ocr_status": "pending" }
    """
    import os
    from werkzeug.utils import secure_filename

    client = g.client
    file   = request.files.get("file")

    if not file or file.filename == "":
        return error("No file provided.")

    ext = _get_extension(file.filename)
    if ext not in current_app.config.get("ALLOWED_IMAGE_EXTENSIONS", {"jpg","jpeg","png","pdf"}):
        return error("Invalid file type. Please upload a JPG, PNG, or PDF.")

    passport_id    = str(uuid.uuid4())
    upload_folder  = os.path.join(current_app.config["UPLOAD_FOLDER"], "passports", client.client_id)
    os.makedirs(upload_folder, exist_ok=True)

    safe_name = secure_filename(f"passport_{passport_id[:8]}.{ext}")
    file_path = os.path.join(upload_folder, safe_name)
    file.save(file_path)

    passport = Passport(
        passport_id = passport_id,
        client_id   = client.client_id,
        image_path  = file_path,
    )
    db.session.add(passport)

    # Advance client status
    if client.status == ClientStatus.pending:
        client.status = ClientStatus.id_uploaded

    db.session.commit()

    # Enqueue OCR task (Step 11)
    try:
        from tasks.process_docs import run_ocr
        run_ocr.delay(client.client_id, "passport", file_path, passport_id)
    except Exception:
        current_app.logger.warning("Celery not available — OCR will not run automatically.")

    preview_url = f"/api/documents/preview/passport/{passport_id}"

    return success(
        data={"passport_id": passport_id, "preview_url": preview_url, "ocr_status": "pending"},
        status_code=201,
    )


@client_bp.route("/upload/passport/<passport_id>", methods=["DELETE"])
@client_token_auth
def delete_passport(passport_id):
    """DELETE /client/upload/passport/<passport_id>"""
    import os
    client   = g.client
    passport = Passport.query.filter_by(passport_id=passport_id, client_id=client.client_id).first()
    if not passport:
        return not_found("Passport")

    if passport.image_path and os.path.exists(passport.image_path):
        os.remove(passport.image_path)

    db.session.delete(passport)
    db.session.commit()
    return success(data={"passport_id": passport_id, "deleted": True})


@client_bp.route("/upload/emirates-id", methods=["POST"])
@client_token_auth
def upload_emirates_id():
    """
    POST /client/upload/emirates-id
    Same flow as passport upload but for Emirates ID.
    """
    import os
    from werkzeug.utils import secure_filename
    from models import EmiratesID

    client = g.client
    file   = request.files.get("file")

    if not file or file.filename == "":
        return error("No file provided.")

    ext = _get_extension(file.filename)
    if ext not in current_app.config.get("ALLOWED_IMAGE_EXTENSIONS", {"jpg","jpeg","png","pdf"}):
        return error("Invalid file type.")

    id_record_id  = str(uuid.uuid4())
    upload_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "emirates_ids", client.client_id)
    os.makedirs(upload_folder, exist_ok=True)

    safe_name = secure_filename(f"emirates_id_{id_record_id[:8]}.{ext}")
    file_path = os.path.join(upload_folder, safe_name)
    file.save(file_path)

    eid = EmiratesID(
        id_record_id = id_record_id,
        client_id    = client.client_id,
        image_path   = file_path,
    )
    db.session.add(eid)
    db.session.commit()

    return success(
        data={"id_record_id": id_record_id, "preview_url": f"/api/ocr/preview/eid/{id_record_id}", "ocr_status": "pending"},
        status_code=201,
    )


# ════════════════════════════════════════════════════════════
#  STEP 3 — Statement
# ════════════════════════════════════════════════════════════

@client_bp.route("/statement/text", methods=["POST"])
@client_token_auth
def submit_text_statement():
    """
    POST /client/statement/text
    Body: { "text": str }
    Creates a Statement record with client_edited_text.
    """
    client = g.client
    body   = request.get_json() or {}
    text   = (body.get("text") or "").strip()

    if not text:
        return error("Statement text cannot be empty.")

    count = Statement.query.filter_by(client_id=client.client_id).count()
    if count >= 3:
        return error("Maximum 3 statements allowed. Your lawyer will follow up if more info is needed.")

    stmt = Statement(
        statement_id      = str(uuid.uuid4()),
        client_id         = client.client_id,
        sequence_number   = count + 1,
        client_edited_text = text,
        channel           = StatementChannel.web,
    )
    db.session.add(stmt)
    db.session.commit()

    return success(data={
        "statement_id":         stmt.statement_id,
        "sequence_number":      stmt.sequence_number,
        "statements_remaining": max(0, 3 - (count + 1)),
    }, status_code=201)


@client_bp.route("/statement/audio", methods=["POST"])
@client_token_auth
def upload_audio():
    """
    POST /client/statement/audio
    Form: audio_file

    Saves audio, creates Statement record with raw_audio_path.
    Whisper transcription queued as Celery task (Step 14).
    """
    import os
    from werkzeug.utils import secure_filename
    from utils.naming import make_audio_filename

    client = g.client
    file   = request.files.get("audio_file")
    if not file or file.filename == "":
        return error("No audio file provided.")

    count = Statement.query.filter_by(client_id=client.client_id).count()
    if count >= 3:
        return error("Maximum 3 statements allowed.")

    ext = _get_extension(file.filename) or "webm"
    seq = count + 1
    upload_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "audio", client.client_id)
    os.makedirs(upload_folder, exist_ok=True)

    fname     = make_audio_filename(client.client_id, seq, ext)
    file_path = os.path.join(upload_folder, secure_filename(fname))
    file.save(file_path)

    stmt = Statement(
        statement_id    = str(uuid.uuid4()),
        client_id       = client.client_id,
        sequence_number = seq,
        raw_audio_path  = file_path,
        channel         = StatementChannel.web,
    )
    db.session.add(stmt)
    db.session.commit()

    # Enqueue Whisper transcription (Step 14)
    try:
        from tasks.process_docs import transcribe_statement
        transcribe_statement.delay(stmt.statement_id, file_path)
    except Exception:
        current_app.logger.warning("Celery not available — Whisper transcription will not run automatically.")

    return success(data={
        "statement_id":          stmt.statement_id,
        "sequence_number":       seq,
        "transcription_status":  "pending",
    }, status_code=201)


@client_bp.route("/statement/<statement_id>/transcription", methods=["GET"])
@client_token_auth
def get_transcription(statement_id):
    """Poll for Whisper transcription result."""
    client = g.client
    stmt   = Statement.query.filter_by(statement_id=statement_id, client_id=client.client_id).first()
    if not stmt:
        return not_found("Statement")

    if stmt.whisper_transcription:
        return success(data={"statement_id": statement_id, "status": "done", "text": stmt.whisper_transcription})
    return success(data={"statement_id": statement_id, "status": "pending", "text": None})


@client_bp.route("/statement/<statement_id>/confirm", methods=["POST"])
@client_token_auth
def confirm_statement(statement_id):
    """
    POST /client/statement/<statement_id>/confirm
    Body: { "client_edited_text": str }
    Client confirms or edits the transcription.
    """
    client = g.client
    body   = request.get_json() or {}
    stmt   = Statement.query.filter_by(statement_id=statement_id, client_id=client.client_id).first()
    if not stmt:
        return not_found("Statement")

    text = (body.get("client_edited_text") or "").strip()
    if not text:
        return error("Statement text cannot be empty.")

    stmt.client_edited_text = text
    db.session.commit()

    total = Statement.query.filter_by(client_id=client.client_id).count()
    return success(data={
        "statement_id":         statement_id,
        "sequence_number":      stmt.sequence_number,
        "statements_remaining": max(0, 3 - total),
    })


# ════════════════════════════════════════════════════════════
#  STEP 3 — Complete (advance to Step 4)
# ════════════════════════════════════════════════════════════

@client_bp.route("/statement/complete", methods=["POST"])
@client_token_auth
def statement_complete():
    """
    POST /client/statement/complete?token=...
    Called when client clicks Continue on Step 3.
    Validates at least one confirmed statement exists.
    Advances status and returns next_url for Step 4.
    """
    client = g.client
    token  = request.args.get("token", "")

    # Must have at least one confirmed statement (client_edited_text set)
    confirmed = [s for s in client.statements if s.client_edited_text]
    if not confirmed:
        return error("Please provide at least one statement before continuing.")

    # Advance status toward document collection
    advanceable = (
        ClientStatus.conflict_check,
        ClientStatus.context_collection,
        ClientStatus.id_uploaded,
    )
    if client.status in advanceable:
        client.status = ClientStatus.review
        _write_audit(
            client.firm_id,
            f"Client '{client.full_name}' completed statement step ({len(confirmed)} statement(s)).",
            "client", client.client_id,
        )
        db.session.commit()

    next_url = f"/client/{client.reference_id}/documents?token={token}"
    return success(data={"next_url": next_url})


# ── List existing statements (page restore on reload) ─────

@client_bp.route("/statement/list", methods=["GET"])
@client_token_auth
def list_statements():
    """
    GET /client/statement/list?token=...
    Returns existing statements for the client.
    Used when returning client reloads the statement page.
    """
    client = g.client
    return success(data={
        "statements": [
            {
                "statement_id":       s.statement_id,
                "sequence_number":    s.sequence_number,
                "client_edited_text": s.client_edited_text,
                "has_audio":          bool(s.raw_audio_path),
                "channel":            s.channel.value,
                "confirmed":          bool(s.client_edited_text),
            }
            for s in sorted(client.statements, key=lambda x: x.sequence_number)
        ],
        "total_confirmed": sum(1 for s in client.statements if s.client_edited_text),
    })


# ════════════════════════════════════════════════════════════
#  STEP 4 — Documents
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
#  STEP 2 — Complete (advance to Step 3)
# ════════════════════════════════════════════════════════════

@client_bp.route("/upload/complete", methods=["POST"])
@client_token_auth
def upload_complete():
    """
    POST /client/upload/complete?token=...
    Called when client clicks Continue on Step 2.
    Validates at least one passport uploaded, advances status,
    triggers background OCR + conflict check (Steps 11/12).

    Response: { "next_url": "/client/<ref>/statement?token=..." }
    """
    client = g.client
    token  = request.args.get("token", "")

    if not client.passports:
        return error("Please upload at least one passport before continuing.")

    # Advance status to conflict_check (OCR + check runs in background)
    if client.status in (ClientStatus.pending, ClientStatus.id_uploaded):
        client.status = ClientStatus.conflict_check
        _write_audit(
            client.firm_id,
            f"Client '{client.full_name}' completed ID upload step.",
            "client", client.client_id,
        )
        db.session.commit()

        # Enqueue OCR for each passport and conflict check (Steps 11/12)
        try:
            from tasks.process_docs import run_ocr
            for p in client.passports:
                run_ocr.delay(client.client_id, "passport", p.image_path, p.passport_id)
        except Exception:
            current_app.logger.warning("Celery not available — OCR/conflict check will not run automatically.")

    next_url = f"/client/{client.reference_id}/statement?token={token}"
    return success(data={"next_url": next_url})


# ── List uploaded passports (for page reload / returning client) ──

@client_bp.route("/upload/passports", methods=["GET"])
@client_token_auth
def list_passports():
    """
    GET /client/upload/passports?token=...
    Returns all passports already uploaded for this client.
    Used when a returning client reloads the upload page.
    """
    client = g.client
    return success(data={
        "passports": [
            {
                "passport_id":     p.passport_id,
                "passport_number": p.passport_number,
                "nationality":     p.nationality,
                "ocr_done":        bool(p.passport_number),
            }
            for p in client.passports
        ],
        "emirates_id": {
            "id_record_id": client.emirates_ids[0].id_record_id,
            "id_number":    client.emirates_ids[0].id_number,
        } if client.emirates_ids else None,
    })


@client_bp.route("/documents/upload", methods=["POST"])
@client_token_auth
def upload_document():
    """
    POST /client/documents/upload
    Form: file, document_type, request_id (optional)
    """
    import os
    from werkzeug.utils import secure_filename
    from datetime import date

    client   = g.client
    file     = request.files.get("file")
    doc_type_str = request.form.get("document_type", "other")
    request_id   = request.form.get("request_id")

    if not file or file.filename == "":
        return error("No file provided.")

    try:
        doc_type = DocumentCategory[doc_type_str.lower().replace(" ", "_")]
    except KeyError:
        doc_type = DocumentCategory.other

    ext = _get_extension(file.filename) or "pdf"
    saved_name = make_document_filename(client.full_name, doc_type.value, ext)

    upload_folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "documents", client.client_id)
    os.makedirs(upload_folder, exist_ok=True)

    file_path = os.path.join(upload_folder, secure_filename(saved_name))
    # Avoid overwrites — append short uuid if name exists
    if os.path.exists(file_path):
        saved_name = f"{saved_name.rsplit('.', 1)[0]}_{str(uuid.uuid4())[:6]}.{ext}"
        file_path  = os.path.join(upload_folder, secure_filename(saved_name))

    file.save(file_path)

    doc = Document(
        document_id       = str(uuid.uuid4()),
        client_id         = client.client_id,
        original_filename = file.filename,
        saved_filename    = saved_name,
        file_path         = file_path,
        file_type         = doc_type,
        requested_by_firm = bool(request_id),
    )
    db.session.add(doc)

    # Mark requested doc as received
    if request_id:
        req_doc = RequestedDocument.query.filter_by(
            request_id=request_id, client_id=client.client_id
        ).first()
        if req_doc:
            req_doc.is_received           = True
            req_doc.received_document_id  = doc.document_id

    db.session.commit()

    return success(data={
        "document_id":    doc.document_id,
        "saved_filename": saved_name,
        "file_type":      doc_type.value,
    }, status_code=201)


@client_bp.route("/documents/<document_id>", methods=["DELETE"])
@client_token_auth
def delete_document(document_id):
    import os
    client = g.client
    doc    = Document.query.filter_by(document_id=document_id, client_id=client.client_id).first()
    if not doc:
        return not_found("Document")

    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    # Re-open checklist item if applicable
    req = RequestedDocument.query.filter_by(received_document_id=document_id).first()
    if req:
        req.is_received          = False
        req.received_document_id = None

    db.session.delete(doc)
    db.session.commit()
    return success(data={"document_id": document_id, "deleted": True})


@client_bp.route("/documents/<document_id>/category", methods=["PUT"])
@client_token_auth
def update_document_category(document_id):
    client = g.client
    body   = request.get_json() or {}
    doc    = Document.query.filter_by(document_id=document_id, client_id=client.client_id).first()
    if not doc:
        return not_found("Document")

    try:
        doc.file_type = DocumentCategory[body.get("file_type", "other").lower().replace(" ", "_")]
    except KeyError:
        doc.file_type = DocumentCategory.other

    db.session.commit()
    return success(data={"document_id": document_id, "file_type": doc.file_type.value})


# ════════════════════════════════════════════════════════════
#  FINAL SUBMISSION
# ════════════════════════════════════════════════════════════

@client_bp.route("/submit", methods=["POST"])
@client_token_auth
def submit():
    """
    POST /client/submit
    Marks intake as complete. Triggers AI brief generation (Step 16).
    """
    client = g.client

    # Validate minimum requirements
    if not client.passports:
        return error("Please upload at least one passport before submitting.")
    if not client.statements:
        return error("Please provide at least one statement before submitting.")

    client.status = ClientStatus.review

    _write_audit(
        client.firm_id,
        f"Client '{client.full_name}' ({client.reference_id}) submitted intake.",
        "client", client.client_id,
    )
    db.session.commit()

    # Enqueue AI brief generation (Step 16)
    try:
        from tasks.process_docs import generate_ai_brief
        generate_ai_brief.delay(client.client_id)
    except Exception:
        current_app.logger.warning("Celery not available — AI brief will not be generated automatically.")

    return success(data={
        "reference_id": client.reference_id,
        "message":      "Your submission has been received. Your lawyer will review it shortly.",
    })


# ════════════════════════════════════════════════════════════
#  CLIENT EDIT (returning clients)
# ════════════════════════════════════════════════════════════

@client_bp.route("/edit/profile", methods=["PUT"])
@client_token_auth
def edit_profile():
    """
    PUT /client/edit/profile
    Body: { "full_name", "email", "phone" }
    All edits logged. Passport number changes re-trigger conflict check.
    """
    from models import ClientEdit
    client = g.client
    body   = request.get_json() or {}

    editable          = ["full_name", "email", "phone"]
    name_changed      = False
    recheck_triggered = False

    for field in editable:
        new_val = body.get(field)
        if new_val is not None:
            new_val = new_val.strip()
            old_val = getattr(client, field)
            if str(old_val) != str(new_val):
                if field == "full_name":
                    name_changed = True
                edit = ClientEdit(
                    edit_id=str(uuid.uuid4()),
                    client_id=client.client_id,
                    field_changed=field,
                    old_value=str(old_val),
                    new_value=str(new_val),
                    re_conflict_check_triggered=name_changed,
                )
                db.session.add(edit)
                setattr(client, field, new_val)

    db.session.commit()

    # Re-trigger conflict check if name changed (embedding will differ)
    if name_changed:
        try:
            from tasks.process_docs import run_conflict_check
            run_conflict_check.delay(client.client_id)
            recheck_triggered = True
        except Exception:
            pass

    return success(data={"recheck_triggered": recheck_triggered}, message="Profile updated.")


# ════════════════════════════════════════════════════════════
#  Private helpers
# ════════════════════════════════════════════════════════════

def _get_extension(filename: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""


def _get_default_firm_id() -> str | None:
    """Return the first firm in the DB — used for self-service portal."""
    from models import LawFirm
    firm = LawFirm.query.order_by(LawFirm.created_at).first()
    return firm.firm_id if firm else None


def _write_audit(firm_id, action, record_type=None, record_id=None, performed_by=None):
    import uuid as _uuid
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


def _queue_whatsapp_greeting(client, portal_link: str):
    """
    Queue the initial WhatsApp greeting message.
    Full send implementation in Step 13.
    """
    current_app.logger.info(
        f"[WA STUB] Would send greeting to {client.phone} — ref {client.reference_id} — {portal_link}"
    )
