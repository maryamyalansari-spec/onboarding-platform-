"""
process_docs.py — Background Celery tasks for document processing.

Step 11: run_ocr        — PaddleOCR on passport / Emirates ID
Step 12: run_conflict_check — Full 3-tier conflict check
Step 16: generate_ai_brief  — GPT-4 brief generation
"""

import logging
from tasks.celery_app import celery

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
#  Step 11 — PaddleOCR
# ════════════════════════════════════════════════════════════

@celery.task(bind=True, name="tasks.run_ocr", max_retries=2)
def run_ocr(self, client_id: str, document_type: str, file_path: str, record_id: str = None):
    """
    Run PaddleOCR on a passport or Emirates ID image.

    Args:
        client_id:     Client UUID
        document_type: "passport" or "emirates_id"
        file_path:     Absolute path to the uploaded image
        record_id:     passport_id or id_record_id to update

    Saves extracted fields to the relevant DB record, builds a conflict
    check payload via normalise_ocr_output(), and triggers run_conflict_check
    once OCR is complete (if all passports for the client are done).
    """
    import os
    from database import db
    from models import Passport, EmiratesID, Client
    from utils.ocr import extract_text_blocks, extract_passport_fields, extract_emirates_id_fields
    from utils.conflict_schema import normalise_ocr_output

    logger.info(f"[OCR] Starting {document_type} OCR for client {client_id}, record {record_id}")

    if not os.path.exists(file_path):
        logger.error(f"[OCR] File not found: {file_path}")
        raise FileNotFoundError(f"OCR file not found: {file_path}")

    try:
        texts = extract_text_blocks(file_path)
        logger.info(f"[OCR] Extracted {len(texts)} text blocks from {file_path}")
    except Exception as exc:
        logger.exception(f"[OCR] PaddleOCR failed: {exc}")
        raise self.retry(exc=exc, countdown=15)

    if document_type == "passport":
        fields = extract_passport_fields(texts)
        logger.info(f"[OCR] Passport fields: {fields}")

        if record_id:
            passport = Passport.query.get(record_id)
            if passport:
                passport.passport_number = fields.get("passport_number")
                passport.nationality     = fields.get("nationality")
                passport.date_of_birth   = fields.get("date_of_birth")
                passport.expiry_date     = fields.get("expiry_date")
                # Store full extraction (including name, gender, etc.) in JSONB
                passport.ocr_raw = {
                    **fields,
                    "raw_texts": texts,
                }
                db.session.commit()
                logger.info(f"[OCR] Passport {record_id} updated with OCR data.")

        # Build conflict schema from OCR output
        ocr_raw = {
            "full_name":       fields.get("full_name"),
            "passport_number": fields.get("passport_number"),
            "nationality":     fields.get("nationality"),
        }
        conflict_payload = normalise_ocr_output(ocr_raw, source_file=file_path)

    elif document_type == "emirates_id":
        fields = extract_emirates_id_fields(texts)
        logger.info(f"[OCR] Emirates ID fields: {fields}")

        if record_id:
            eid = EmiratesID.query.get(record_id)
            if eid:
                eid.id_number = fields.get("id_number")
                eid.ocr_raw   = {
                    **fields,
                    "raw_texts": texts,
                }
                db.session.commit()
                logger.info(f"[OCR] Emirates ID {record_id} updated with OCR data.")

        ocr_raw = {
            "full_name":   fields.get("full_name"),
            "id_number":   fields.get("id_number"),
            "nationality": fields.get("nationality"),
        }
        conflict_payload = normalise_ocr_output(ocr_raw, source_file=file_path)

    else:
        logger.warning(f"[OCR] Unknown document_type: {document_type}")
        return {"status": "skipped", "reason": f"unknown document_type: {document_type}"}

    # Check if all passports for this client have been OCR'd
    client = Client.query.get(client_id)
    if client:
        all_done = all(
            p.passport_number is not None
            for p in client.passports
        )
        if all_done and client.passports:
            logger.info(f"[OCR] All passports done for {client_id}, queuing conflict check.")
            run_conflict_check.delay(client_id)  # will notify WhatsApp on completion

    return {
        "status":           "done",
        "document_type":    document_type,
        "record_id":        record_id,
        "extracted":        fields,
        "conflict_payload": conflict_payload,
    }


# ════════════════════════════════════════════════════════════
#  Step 14 — Whisper transcription
# ════════════════════════════════════════════════════════════

@celery.task(bind=True, name="tasks.transcribe_statement", max_retries=2)
def transcribe_statement(self, statement_id: str, audio_path: str):
    """
    Transcribe an audio statement file using Whisper and save the result.

    Args:
        statement_id: UUID of the Statement record to update
        audio_path:   Absolute path to the audio file
    """
    from database import db
    from models import Statement

    logger.info(f"[Whisper] Transcribing statement {statement_id}: {audio_path}")

    try:
        from routes.ai import transcribe_audio
        text = transcribe_audio(audio_path)
    except Exception as exc:
        logger.exception(f"[Whisper] Transcription failed for {statement_id}: {exc}")
        raise self.retry(exc=exc, countdown=20)

    stmt = Statement.query.get(statement_id)
    if not stmt:
        logger.warning(f"[Whisper] Statement {statement_id} not found — skipping save.")
        return {"status": "statement_not_found"}

    stmt.whisper_transcription = text
    # If client hasn't edited yet, set client_edited_text as default
    if not stmt.client_edited_text:
        stmt.client_edited_text = text
    db.session.commit()

    logger.info(f"[Whisper] Statement {statement_id} transcribed ({len(text)} chars).")

    # If WhatsApp channel — send transcription back to the client for confirmation
    if stmt.channel and stmt.channel.value == "whatsapp":
        try:
            from models import Client
            client = Client.query.get(stmt.client_id)
            if client:
                from routes.whatsapp import _send
                preview = text[:300] + ("…" if len(text) > 300 else "")
                _send(client.phone,
                    f"📝 Transcription of your voice note:\n\n_{preview}_\n\n"
                    "Reply *1* to confirm, *2* to re-record, or type *edit: <your text>* to modify."
                )
        except Exception as e:
            logger.warning(f"[Whisper] WA notification failed: {e}")

    return {"status": "done", "statement_id": statement_id, "chars": len(text)}


# ════════════════════════════════════════════════════════════
#  Step 12 — Conflict check  (stub — implemented in Step 12)
# ════════════════════════════════════════════════════════════

@celery.task(bind=True, name="tasks.run_conflict_check", max_retries=2)
def run_conflict_check(self, client_id: str):
    """
    Run the full 3-tier conflict check for a client.
    Implemented in Step 12 via utils/conflict_check.py.
    """
    try:
        from utils.conflict_check import run_conflict_check as _check
        result = _check(client_id)
        logger.info(f"[Celery] Conflict check done for {client_id}: {result}")

        # Notify WhatsApp client if applicable (Step 15)
        try:
            from models import Client
            client = Client.query.get(client_id)
            if client and client.channel and client.channel.value == "whatsapp":
                from routes.whatsapp import notify_conflict_result
                notify_conflict_result(client, float(result.get("confidence_score", 0)))
        except Exception as e:
            logger.warning(f"[Celery] WA notification after conflict check failed: {e}")

        # Send conflict-clear email if no conflict found (Step 25)
        try:
            score = float(result.get("confidence_score", 0))
            if score < 50:  # clear
                from tasks.notifications import send_conflict_clear_email
                send_conflict_clear_email.delay(client_id)
        except Exception as e:
            logger.warning(f"[Celery] Conflict-clear email failed: {e}")

        return result
    except Exception as exc:
        logger.exception(f"[Celery] Conflict check failed for {client_id}: {exc}")
        raise self.retry(exc=exc, countdown=30)


# ════════════════════════════════════════════════════════════
#  Step 16 — AI brief  (stub — implemented in Step 16)
# ════════════════════════════════════════════════════════════

@celery.task(bind=True, name="tasks.generate_ai_brief", max_retries=2)
def generate_ai_brief(self, client_id: str):
    """
    Generate an AI brief for a client using GPT-4 and save to ai_briefs table.
    Implemented in Step 16 via routes/ai.py generate_brief().
    """
    import uuid as _uuid
    from database import db
    from models import Client, AIBrief, ConflictResult

    logger.info(f"[AI Brief] Generating brief for client {client_id}")

    client = Client.query.get(client_id)
    if not client:
        logger.error(f"[AI Brief] Client {client_id} not found.")
        return {"status": "client_not_found"}

    # Gather conflict score
    latest_conflict = (
        ConflictResult.query
        .filter_by(client_id=client_id)
        .order_by(ConflictResult.created_at.desc())
        .first()
    )
    conflict_score = float(latest_conflict.confidence_score) if latest_conflict else 0.0

    client_data = {
        "full_name":      client.full_name,
        "conflict_score": conflict_score,
        "statements": [
            {
                "sequence_number":   s.sequence_number,
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
            for p in client.passports
            if p.nationality
        ],
    }

    try:
        from routes.ai import generate_brief
        result = generate_brief(client_data)
    except Exception as exc:
        logger.exception(f"[AI Brief] GPT-4 failed for {client_id}: {exc}")
        raise self.retry(exc=exc, countdown=30)

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

    logger.info(f"[AI Brief] Brief {brief.brief_id} generated for client {client_id}.")
    return {"status": "done", "brief_id": brief.brief_id}
