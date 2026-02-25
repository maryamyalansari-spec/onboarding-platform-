"""
ai.py — ALL AI calls are isolated here.

Current providers:
  - Speech-to-text:  OpenAI Whisper API
  - Analysis:        Anthropic Claude (claude-sonnet-4-6)
  - Embeddings:      OpenAI text-embedding-3-small (for conflict soft-match)
"""

import os
from flask import Blueprint

ai_bp = Blueprint("ai", __name__)


# ─── Speech-to-text ───────────────────────────────────────────────────────────

def transcribe_audio(audio_file_path: str) -> str:
    """
    Transcribe an audio file to text.

    Current provider: OpenAI Whisper API (whisper-1)
    Swap target:      faster-whisper (self-hosted)

    Args:
        audio_file_path: Absolute path to the audio file

    Returns:
        Transcribed text string

    Raises:
        RuntimeError if transcription fails
    """
    try:
        import openai
        from flask import current_app
        api_key = current_app.config.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured.")

        client = openai.OpenAI(api_key=api_key)
        with open(audio_file_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="en",   # hint — Whisper auto-detects if wrong
            )
        return transcript.text
    except FileNotFoundError:
        raise RuntimeError(f"Audio file not found: {audio_file_path}")
    except Exception as exc:
        raise RuntimeError(f"Whisper transcription failed: {exc}") from exc


# ─── AI Analysis (Anthropic Claude) ───────────────────────────────────────────

def generate_brief(client_data: dict) -> dict:
    """
    Generate a structured AI analysis for a client using Anthropic Claude.

    Args:
        client_data: Dict containing:
            - full_name:   str
            - statements:  list of { sequence_number, client_edited_text }
            - documents:   list of { file_type, saved_filename }
            - passports:   list of { nationality }

    Returns:
        Dict with keys matching AIBrief model fields.
    """
    import json as _json
    try:
        import anthropic
        from flask import current_app
        api_key = current_app.config.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured. Add it to your .env file.")

        client_name = client_data.get("full_name", "Unknown")
        statements  = client_data.get("statements", [])
        documents   = client_data.get("documents", [])
        passports   = client_data.get("passports", [])

        stmt_text = "\n".join(
            f"Statement {s['sequence_number']}: {s['client_edited_text']}"
            for s in statements if s.get("client_edited_text")
        ) or "No statements provided."

        doc_text = "\n".join(
            f"- {d.get('file_type', 'Unknown type')}: {d.get('saved_filename', '')}"
            for d in documents
        ) or "No documents uploaded."

        nat_text = ", ".join(
            p.get("nationality", "") for p in passports if p.get("nationality")
        ) or "Unknown"

        prompt = f"""You are a legal intake assistant at a UAE law firm. Analyse the following client intake and produce a structured assessment for the reviewing lawyer. Be concise, professional, and objective. Output ONLY valid JSON — no markdown, no preamble.

CLIENT: {client_name}
NATIONALITY: {nat_text}

CLIENT STATEMENTS:
{stmt_text}

DOCUMENTS UPLOADED:
{doc_text}

Produce a JSON object with exactly these fields:
{{
  "client_summary": "2-3 sentence overview of who the client is and their matter",
  "situation_overview": "A clear paragraph explaining the legal situation as described by the client",
  "key_facts": ["fact 1", "fact 2", "fact 3"],
  "documents_provided": ["list of document types uploaded"],
  "inconsistencies": "Any inconsistencies or notable gaps in the client's account, or null if none",
  "questions_for_lawyer": ["question 1", "question 2"],
  "risk_notes": "Any risk or complexity notes the lawyer should be aware of, or null if none"
}}"""

        claude = anthropic.Anthropic(api_key=api_key)
        message = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        # Strip any accidental markdown fences
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = _json.loads(clean.strip())

        return {
            "client_summary":       parsed.get("client_summary"),
            "situation_overview":   parsed.get("situation_overview"),
            "key_facts":            parsed.get("key_facts", []),
            "documents_provided":   parsed.get("documents_provided", []),
            "inconsistencies":      parsed.get("inconsistencies"),
            "questions_for_lawyer": parsed.get("questions_for_lawyer", []),
            "risk_notes":           parsed.get("risk_notes"),
            "raw_gpt_response":     raw,
        }
    except Exception as exc:
        raise RuntimeError(f"Claude analysis failed: {exc}") from exc


# ─── Embeddings ───────────────────────────────────────────────────────────────

def generate_embedding(text: str) -> list:
    """
    Generate a vector embedding for text.

    Current provider: OpenAI text-embedding-3-small (1536 dimensions)
    Swap target:      sentence-transformers (local)

    Args:
        text: The text to embed (typically a person's full name)

    Returns:
        List of 1536 floats

    Raises:
        RuntimeError if embedding generation fails
    """
    try:
        import openai
        from flask import current_app
        api_key = current_app.config.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured.")
        client = openai.OpenAI(api_key=api_key)
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text.strip(),
        )
        return response.data[0].embedding
    except Exception as exc:
        raise RuntimeError(f"Embedding generation failed: {exc}") from exc
