"""
ai.py — ALL AI/OpenAI calls are isolated here.

This is the ONLY file that imports or calls OpenAI (Whisper + GPT-4).
To swap to local Ollama models, change only this file. Nothing else changes.

Current providers:
  - Speech-to-text:  OpenAI Whisper API
  - Brief generation: OpenAI GPT-4
  - Embeddings:      OpenAI text-embedding-3-small (for conflict soft-match)

Future swap targets:
  - Speech-to-text:  faster-whisper (self-hosted)
  - Brief generation: Ollama (local LLM via HTTP)
  - Embeddings:      sentence-transformers (local)

Full implementation:
  - Whisper:    Step 14
  - GPT-4 brief: Step 16
  - Embeddings: Step 12
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


# ─── Brief generation ─────────────────────────────────────────────────────────

def generate_brief(client_data: dict) -> dict:
    """
    Generate a structured AI brief for a client using GPT-4.

    Current provider: OpenAI GPT-4o
    Swap target:      Ollama local LLM

    Args:
        client_data: Dict containing:
            - full_name:       str
            - statements:      list of { sequence_number, client_edited_text }
            - documents:       list of { file_type, saved_filename }
            - passports:       list of { nationality }
            - conflict_score:  float (0–100)

    Returns:
        Dict with keys matching AIBrief model fields.

    Raises:
        RuntimeError if generation fails
    """
    import json as _json
    try:
        import openai
        from flask import current_app
        api_key = current_app.config.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured.")

        client_name = client_data.get("full_name", "Unknown")
        statements  = client_data.get("statements", [])
        documents   = client_data.get("documents", [])
        passports   = client_data.get("passports", [])
        conflict_score = client_data.get("conflict_score", 0)

        # Build the prompt
        stmt_text = "\n".join(
            f"Statement {s['sequence_number']}: {s['client_edited_text']}"
            for s in statements if s.get("client_edited_text")
        ) or "No statements provided."

        doc_text = ", ".join(d.get("file_type", "unknown") for d in documents) or "None"
        nat_text = ", ".join(p.get("nationality", "") for p in passports if p.get("nationality")) or "Unknown"

        system_prompt = (
            "You are a legal intake assistant at a UAE law firm. "
            "Your task is to read a client's self-reported information and produce a structured "
            "brief for the reviewing lawyer. Be concise, professional, and objective. "
            "Output ONLY valid JSON — no markdown, no preamble."
        )

        user_prompt = f"""
Client: {client_name}
Nationality: {nat_text}
Conflict check score: {conflict_score}/100

Client statements:
{stmt_text}

Documents uploaded: {doc_text}

Produce a JSON object with exactly these fields:
{{
  "client_summary": "2-3 sentence overview of who the client is and their matter",
  "situation_overview": "Paragraph explaining the legal situation as described",
  "key_facts": ["fact 1", "fact 2", ...],
  "documents_provided": ["doc type 1", ...],
  "inconsistencies": "Any inconsistencies or gaps in the client's account, or null",
  "questions_for_lawyer": ["question 1", ...],
  "risk_notes": "Risk or complexity notes for the lawyer, or null"
}}
""".strip()

        oai = openai.OpenAI(api_key=api_key)
        response = oai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content
        parsed = _json.loads(raw)

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
        raise RuntimeError(f"GPT-4 brief generation failed: {exc}") from exc


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
