"""
whatsapp.py — Twilio WhatsApp webhook handler.

State machine
─────────────
  greeting          → sent welcome, waiting for any reply
  contact_info      → collecting full_name then email (phone known from WA)
  passport_upload   → waiting for passport image(s); "done" advances
  conflict_pending  → OCR/conflict running; client is asked to wait
  statement_1       → collecting first statement (text or voice)
  statement_1_confirm → showing transcription, asking confirmation
  statement_2 / statement_2_confirm → second statement
  statement_3 / statement_3_confirm → third (max) statement
  document_upload   → collecting supporting documents; "done" submits
  document_categorize (handled inline)
  completed         → thanked, portal link sent

Outbound messages are sent via Twilio REST API (not TwiML).
Webhook always returns empty TwiML <Response/>.
"""

import os
import uuid
import logging
import requests as _requests
from flask import Blueprint, request, current_app

from database import db
from models import (
    Client, ClientStatus, ClientChannel,
    WhatsAppState, Passport, EmiratesID, Statement,
    StatementChannel, Document, DocumentCategory,
    AuditLog,
)
from utils.reference import generate_reference_id, generate_portal_token, token_expiry

whatsapp_bp = Blueprint("whatsapp", __name__)
logger = logging.getLogger(__name__)

# Placeholder values for fields not yet collected
_NAME_PLACEHOLDER  = "WA_PENDING_NAME"
_EMAIL_PLACEHOLDER = "wa_pending@placeholder.ae"


# ════════════════════════════════════════════════════════════
#  Main webhook
# ════════════════════════════════════════════════════════════

@whatsapp_bp.route("/whatsapp", methods=["POST"])
def webhook():
    """
    POST /webhook/whatsapp
    All inbound WhatsApp messages arrive here via Twilio.
    """
    from_number = request.form.get("From", "").strip()   # e.g. whatsapp:+971XXXXXXXXX
    body        = request.form.get("Body", "").strip()
    media_url   = request.form.get("MediaUrl0")
    media_type  = request.form.get("MediaContentType0", "")

    if not from_number:
        return _twiml_empty()

    try:
        client = _find_or_create_client(from_number)
        _dispatch(client, body, media_url, media_type)
    except Exception as exc:
        logger.exception(f"[WA] Unhandled error for {from_number}: {exc}")
        _send(from_number, "Sorry, something went wrong. Please try again in a moment.")

    return _twiml_empty()


@whatsapp_bp.route("/whatsapp/status", methods=["POST"])
def delivery_status():
    """POST /webhook/whatsapp/status — Twilio delivery callbacks."""
    sid    = request.form.get("MessageSid", "")
    status = request.form.get("MessageStatus", "")
    to     = request.form.get("To", "")
    logger.info(f"[WA Status] SID={sid} status={status} to={to}")
    return ("", 204)


# ════════════════════════════════════════════════════════════
#  State machine dispatcher
# ════════════════════════════════════════════════════════════

def _dispatch(client: Client, body: str, media_url, media_type: str):
    state = client.whatsapp_state

    if state == WhatsAppState.greeting:
        _handle_greeting(client)

    elif state == WhatsAppState.contact_info:
        _handle_contact_info(client, body)

    elif state == WhatsAppState.passport_upload:
        _handle_passport_upload(client, body, media_url, media_type)

    elif state == WhatsAppState.conflict_pending:
        _handle_conflict_pending(client)

    elif state in (WhatsAppState.statement_1,
                   WhatsAppState.statement_2,
                   WhatsAppState.statement_3):
        _handle_statement_input(client, body, media_url, media_type)

    elif state in (WhatsAppState.statement_1_confirm,
                   WhatsAppState.statement_2_confirm,
                   WhatsAppState.statement_3_confirm):
        _handle_statement_confirm(client, body)

    elif state == WhatsAppState.document_upload:
        _handle_document_upload(client, body, media_url, media_type)

    elif state == WhatsAppState.completed:
        _handle_completed(client)

    else:
        logger.warning(f"[WA] Unknown state {state} for client {client.client_id}")
        _send(client.phone, "Sorry, something went wrong. Please contact us directly.")


# ════════════════════════════════════════════════════════════
#  State handlers
# ════════════════════════════════════════════════════════════

def _handle_greeting(client: Client):
    """Client just registered or messaged in for the first time."""
    _send(client.phone,
        "👋 Welcome to ITIFAQ Legal Services.\n\n"
        "I'll guide you through our quick intake process — it takes about 5 minutes.\n\n"
        "Please start by sending me your *full legal name* as it appears on your passport."
    )
    client.whatsapp_state = WhatsAppState.contact_info
    db.session.commit()


def _handle_contact_info(client: Client, body: str):
    """Collect full_name, then email (phone already known from WA)."""
    if not body:
        _send(client.phone, "Please send a text reply so I can process your information.")
        return

    # Sub-step 1: collect name
    if client.full_name == _NAME_PLACEHOLDER:
        client.full_name = body.strip()
        db.session.commit()
        _send(client.phone,
            f"Thank you, *{client.full_name}*!\n\n"
            "Please send me your *email address* so your lawyer can reach you."
        )
        return

    # Sub-step 2: collect email
    if client.email == _EMAIL_PLACEHOLDER:
        email = body.strip().lower()
        if "@" not in email or "." not in email:
            _send(client.phone, "That doesn't look like a valid email. Please try again.")
            return
        client.email = email
        db.session.commit()

        # All contact info collected — send portal link and ask for passport
        portal_link = f"{_portal_base()}/client/{client.reference_id}?token={client.portal_token}"
        _send(client.phone,
            f"✅ Got it! Your reference number is *{client.reference_id}*.\n\n"
            f"📎 Your secure portal: {portal_link}\n\n"
            "Now please *send a photo* of your *passport* (the data page with your photo). "
            "You can send multiple passports. Send *done* when finished."
        )
        client.whatsapp_state = WhatsAppState.passport_upload
        db.session.commit()
        return

    # Both collected — shouldn't normally reach here
    _send(client.phone, "Your contact information is already saved. Please send your passport photo.")
    client.whatsapp_state = WhatsAppState.passport_upload
    db.session.commit()


def _handle_passport_upload(client: Client, body: str, media_url, media_type: str):
    """Receive passport images or Emirates ID. 'done' advances the flow."""

    # Client typed "done" or "skip"
    if body.lower() in ("done", "next", "skip", "continue"):
        if not client.passports:
            _send(client.phone, "Please upload at least one passport photo first before continuing.")
            return

        _send(client.phone,
            "✅ Passport(s) received. We are now verifying your information — "
            "this usually takes under a minute.\n\n"
            "I'll message you as soon as the check is complete."
        )
        client.status        = ClientStatus.conflict_check
        client.whatsapp_state = WhatsAppState.conflict_pending
        db.session.commit()

        # Trigger OCR + conflict check
        try:
            from tasks.process_docs import run_ocr
            for p in client.passports:
                run_ocr.delay(client.client_id, "passport", p.image_path, p.passport_id)
        except Exception:
            logger.warning("[WA] Celery not available — OCR must be run manually.")
        return

    # Media received
    if media_url and _is_image(media_type):
        success = _save_passport_from_url(client, media_url, media_type)
        if success:
            count = len(client.passports)
            _send(client.phone,
                f"📄 Passport {count} received!\n\n"
                "Send another photo if you have additional passports, or send *done* to continue."
            )
        else:
            _send(client.phone, "Sorry, I couldn't save that image. Please try again.")
        return

    # Non-image, non-"done" text
    _send(client.phone,
        "Please send a *photo* of your passport data page.\n"
        "When you've sent all passports, reply *done*."
    )


def _handle_conflict_pending(client: Client):
    """Tell the client their check is still running."""
    _send(client.phone,
        "⏳ We're still verifying your information. "
        "I'll message you automatically once it's done — usually within 1–2 minutes."
    )


def _handle_statement_input(client: Client, body: str, media_url, media_type: str):
    """Receive a statement as text or voice note."""
    state  = client.whatsapp_state
    seq    = {
        WhatsAppState.statement_1: 1,
        WhatsAppState.statement_2: 2,
        WhatsAppState.statement_3: 3,
    }[state]

    # Voice note (audio)
    if media_url and _is_audio(media_type):
        ext       = _media_ext(media_type)
        file_path = _download_media(media_url, client.client_id, f"stmt_{seq}.{ext}")
        if not file_path:
            _send(client.phone, "Sorry, I couldn't receive your voice note. Please try again or type your statement.")
            return

        stmt = Statement(
            statement_id    = str(uuid.uuid4()),
            client_id       = client.client_id,
            sequence_number = seq,
            raw_audio_path  = file_path,
            channel         = StatementChannel.whatsapp,
        )
        db.session.add(stmt)

        # Advance to confirm state
        _advance_to_confirm(client, seq)
        db.session.commit()

        # Queue Whisper transcription (Step 14)
        try:
            from tasks.process_docs import transcribe_statement
            transcribe_statement.delay(stmt.statement_id, file_path)
        except Exception:
            # Transcription not available yet — ask client to type it
            _send(client.phone,
                "🎤 Voice note received! However, automatic transcription isn't available right now.\n\n"
                "Please *type out your statement* below so we can confirm it:"
            )
            return

        _send(client.phone,
            "🎤 Voice note received! Transcribing now — I'll send you the text to confirm in a moment."
        )
        return

    # Text statement
    if body:
        stmt = Statement(
            statement_id      = str(uuid.uuid4()),
            client_id         = client.client_id,
            sequence_number   = seq,
            client_edited_text = body,
            channel           = StatementChannel.whatsapp,
        )
        db.session.add(stmt)
        _advance_to_confirm(client, seq)
        db.session.commit()

        preview = body[:300] + ("…" if len(body) > 300 else "")
        _send(client.phone,
            f"📝 Statement {seq} received:\n\n_{preview}_\n\n"
            "Reply *1* to confirm this statement, or *2* to re-write it."
        )
        return

    _send(client.phone,
        f"Please type your statement {seq}, or send a voice note."
    )


def _handle_statement_confirm(client: Client, body: str):
    """Confirm or reject a statement."""
    state = client.whatsapp_state
    seq   = {
        WhatsAppState.statement_1_confirm: 1,
        WhatsAppState.statement_2_confirm: 2,
        WhatsAppState.statement_3_confirm: 3,
    }[state]

    # Find the pending statement for this sequence number
    stmt = (Statement.query
            .filter_by(client_id=client.client_id, sequence_number=seq)
            .order_by(Statement.statement_id.desc())
            .first())

    if not stmt:
        logger.warning(f"[WA] No statement {seq} found for client {client.client_id}")
        _send(client.phone, "Something went wrong. Please re-send your statement.")
        _go_back_to_statement(client, seq)
        db.session.commit()
        return

    reply = body.strip()

    # Re-write
    if reply in ("2", "no", "edit", "rewrite", "re-write"):
        db.session.delete(stmt)
        _go_back_to_statement(client, seq)
        db.session.commit()
        _send(client.phone, f"No problem — please re-type your statement {seq}.")
        return

    # Free-text confirmation also accepted (starts with "edit:")
    if reply.lower().startswith("edit:"):
        new_text = reply[5:].strip()
        if new_text:
            stmt.client_edited_text = new_text
            db.session.commit()

    # Default: confirm (1, yes, ok, or just anything else)
    total_confirmed = sum(1 for s in client.statements if s.client_edited_text)

    if seq < 3 and total_confirmed < 3:
        _ask_for_more_statement(client, seq)
        return

    # Max 3 or client said enough — move to documents
    _send(client.phone,
        "✅ Statement(s) saved. Thank you.\n\n"
        "Now please send any *supporting documents* relevant to your matter "
        "(contracts, court letters, licenses, etc.).\n\n"
        "Send each document as an image or PDF. Reply *done* when finished."
    )
    client.whatsapp_state = WhatsAppState.document_upload
    client.status         = ClientStatus.review
    db.session.commit()


def _handle_document_upload(client: Client, body: str, media_url, media_type: str):
    """Receive supporting documents. 'done' submits the intake."""

    if body.lower() in ("done", "submit", "finish", "complete"):
        _send(client.phone,
            f"✅ *Intake complete!*\n\n"
            f"Your reference number is *{client.reference_id}*.\n\n"
            "A lawyer will review your information and contact you within 1–2 business days. "
            f"You can also access your portal at:\n"
            f"{_portal_base()}/client/{client.reference_id}?token={client.portal_token}"
        )
        client.whatsapp_state = WhatsAppState.completed
        client.status         = ClientStatus.review
        db.session.commit()

        # Queue AI brief (Step 16)
        try:
            from tasks.process_docs import generate_ai_brief
            generate_ai_brief.delay(client.client_id)
        except Exception:
            pass
        return

    if media_url and (_is_image(media_type) or _is_document(media_type)):
        ext       = _media_ext(media_type) or "pdf"
        file_path = _download_media(media_url, client.client_id, f"doc_{uuid.uuid4().hex[:6]}.{ext}")
        if not file_path:
            _send(client.phone, "Couldn't receive that file. Please try again.")
            return

        from utils.naming import make_document_filename
        saved_name = make_document_filename(client.full_name, "Document", ext)
        doc = Document(
            document_id       = str(uuid.uuid4()),
            client_id         = client.client_id,
            original_filename = f"whatsapp_doc.{ext}",
            saved_filename    = saved_name,
            file_path         = file_path,
            file_type         = DocumentCategory.other,
            requested_by_firm = False,
        )
        db.session.add(doc)
        db.session.commit()

        count = len(client.documents)
        _send(client.phone,
            f"📎 Document {count} received!\n\n"
            "Send more documents or reply *done* to complete your intake."
        )
        return

    _send(client.phone,
        "Please send a document file or photo, or reply *done* to finish."
    )


def _handle_completed(client: Client):
    """Client messages after completing intake."""
    _send(client.phone,
        f"Your intake is complete (ref: *{client.reference_id}*).\n\n"
        "A lawyer will contact you shortly. If you need to add information, "
        f"please use your portal:\n"
        f"{_portal_base()}/client/{client.reference_id}?token={client.portal_token}"
    )


# ════════════════════════════════════════════════════════════
#  Public helper — called by conflict_check task when done
# ════════════════════════════════════════════════════════════

def notify_conflict_result(client: Client, score: float):
    """
    Called by run_conflict_check after check completes.
    Advances the WhatsApp state to statement_1.
    """
    if client.whatsapp_state != WhatsAppState.conflict_pending:
        return

    if score >= 50:
        _send(client.phone,
            "⚠️ Our team is reviewing your file manually before proceeding. "
            "A lawyer will be in touch directly within 1 business day."
        )
        client.whatsapp_state = WhatsAppState.conflict_pending  # stay pending, admin will resolve
    else:
        _send(client.phone,
            "✅ Verification complete!\n\n"
            "Please describe your legal matter in your own words. "
            "You can send a *text message* or a *voice note*. "
            "Take as much space as you need."
        )
        client.whatsapp_state = WhatsAppState.statement_1
        client.status         = ClientStatus.context_collection

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


# ════════════════════════════════════════════════════════════
#  DB helpers
# ════════════════════════════════════════════════════════════

def _find_or_create_client(from_number: str) -> Client:
    """
    Look up a client by WhatsApp phone number.
    If not found, create a new one and send the greeting.
    """
    # Normalise: strip "whatsapp:" prefix
    phone = from_number.replace("whatsapp:", "").strip()

    client = Client.query.filter_by(phone=phone, channel=ClientChannel.whatsapp).first()
    if client:
        return client

    # Create new client
    firm_id     = _default_firm_id()
    if not firm_id:
        raise RuntimeError("No firm configured for WhatsApp intake.")

    ref_id       = generate_reference_id(firm_id)
    token        = generate_portal_token()
    expires_at   = token_expiry(30)

    client = Client(
        client_id         = str(uuid.uuid4()),
        firm_id           = firm_id,
        reference_id      = ref_id,
        portal_token      = token,
        full_name         = _NAME_PLACEHOLDER,
        email             = _EMAIL_PLACEHOLDER,
        phone             = phone,
        channel           = ClientChannel.whatsapp,
        whatsapp_state    = WhatsAppState.greeting,
        status            = ClientStatus.pending,
        token_expires_at  = expires_at,
    )
    db.session.add(client)
    db.session.commit()
    logger.info(f"[WA] New client created: {ref_id} — {phone}")
    return client


def _save_passport_from_url(client: Client, media_url: str, media_type: str) -> bool:
    """Download a Twilio media URL and create a Passport record."""
    ext = _media_ext(media_type) or "jpg"
    file_path = _download_media(media_url, client.client_id, f"passport_{uuid.uuid4().hex[:8]}.{ext}")
    if not file_path:
        return False

    passport = Passport(
        passport_id = str(uuid.uuid4()),
        client_id   = client.client_id,
        image_path  = file_path,
    )
    db.session.add(passport)

    if client.status == ClientStatus.pending:
        client.status = ClientStatus.id_uploaded

    db.session.commit()
    return True


def _advance_to_confirm(client: Client, seq: int):
    """Set state to statement_N_confirm."""
    mapping = {
        1: WhatsAppState.statement_1_confirm,
        2: WhatsAppState.statement_2_confirm,
        3: WhatsAppState.statement_3_confirm,
    }
    client.whatsapp_state = mapping[seq]


def _go_back_to_statement(client: Client, seq: int):
    """Reset state back to statement_N input."""
    mapping = {
        1: WhatsAppState.statement_1,
        2: WhatsAppState.statement_2,
        3: WhatsAppState.statement_3,
    }
    client.whatsapp_state = mapping[seq]


def _ask_for_more_statement(client: Client, confirmed_seq: int):
    """After confirming a statement, ask if they want to add another."""
    remaining = 3 - confirmed_seq
    next_seq  = confirmed_seq + 1

    mapping = {
        1: WhatsAppState.statement_2,
        2: WhatsAppState.statement_3,
    }

    _send(client.phone,
        f"✅ Statement {confirmed_seq} confirmed!\n\n"
        f"You can add up to {remaining} more statement(s) if needed.\n"
        "Reply *more* to add another, or *done* to continue to document upload."
    )
    # Temporarily set state to a helper — we'll use statement_1_confirm with a flag
    # actually just handle next message: if "more" → set state to next statement
    # if "done" → move to document_upload
    # We store the "asking more" state as statement_N_confirm still,
    # but add a helper check in the confirm handler.
    client.whatsapp_state = mapping.get(confirmed_seq, WhatsAppState.document_upload)
    if confirmed_seq >= 3:
        client.whatsapp_state = WhatsAppState.document_upload
    db.session.commit()


# ════════════════════════════════════════════════════════════
#  Twilio helpers
# ════════════════════════════════════════════════════════════

def _send(to_number: str, message: str):
    """Send a WhatsApp message via Twilio REST API."""
    # Ensure number has whatsapp: prefix
    if not to_number.startswith("whatsapp:"):
        to_number = f"whatsapp:{to_number}"

    try:
        from twilio.rest import Client as TwilioClient
        sid      = current_app.config.get("TWILIO_ACCOUNT_SID")
        token    = current_app.config.get("TWILIO_AUTH_TOKEN")
        wa_from  = current_app.config.get("TWILIO_WHATSAPP_NUMBER")
        if not all([sid, token, wa_from]):
            logger.warning(f"[WA] Twilio not configured — skipping send to {to_number}: {message[:60]}")
            return
        if not wa_from.startswith("whatsapp:"):
            wa_from = f"whatsapp:{wa_from}"
        twilio = TwilioClient(sid, token)
        twilio.messages.create(from_=wa_from, to=to_number, body=message)
    except Exception as exc:
        logger.error(f"[WA] Failed to send message to {to_number}: {exc}")


def _download_media(media_url: str, client_id: str, filename: str) -> str | None:
    """
    Download a Twilio media attachment (image, audio, PDF) to disk.
    Returns the absolute file path, or None on failure.
    """
    try:
        sid   = current_app.config.get("TWILIO_ACCOUNT_SID")
        token = current_app.config.get("TWILIO_AUTH_TOKEN")

        resp = _requests.get(
            media_url,
            auth=(sid, token) if sid and token else None,
            timeout=30,
            stream=True,
        )
        resp.raise_for_status()

        folder = os.path.join(
            current_app.config.get("UPLOAD_FOLDER", "uploads"),
            "whatsapp", client_id,
        )
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, filename)

        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        return file_path
    except Exception as exc:
        logger.error(f"[WA] Media download failed ({media_url}): {exc}")
        return None


def _twiml_empty():
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        200,
        {"Content-Type": "text/xml"},
    )


# ════════════════════════════════════════════════════════════
#  Type detection helpers
# ════════════════════════════════════════════════════════════

def _is_image(mime: str) -> bool:
    return mime.startswith("image/")

def _is_audio(mime: str) -> bool:
    return mime.startswith("audio/")

def _is_document(mime: str) -> bool:
    return mime in (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def _media_ext(mime: str) -> str:
    mapping = {
        "image/jpeg":     "jpg",
        "image/png":      "png",
        "image/gif":      "gif",
        "image/webp":     "webp",
        "audio/ogg":      "ogg",
        "audio/mpeg":     "mp3",
        "audio/mp4":      "m4a",
        "audio/webm":     "webm",
        "application/pdf": "pdf",
    }
    return mapping.get(mime, "bin")

def _default_firm_id() -> str | None:
    from models import LawFirm
    firm = LawFirm.query.order_by(LawFirm.created_at).first()
    return firm.firm_id if firm else None

def _portal_base() -> str:
    cfg = current_app.config
    return cfg.get("PORTAL_BASE_URL", "").rstrip("/")
