"""
conflict_schema.py — The canonical JSON shape used for conflict checking.

This is the single source of truth for what a "conflict check payload" looks like.

BOTH paths that feed the conflict check must produce this shape:
  1. OCR path:     PaddleOCR extracts passport/ID data → normalise_ocr_output()
  2. Manual path:  Client types info in web portal     → normalise_manual_input()

The existing firm DB (JSON files) must also be imported in this shape.
See scripts/import_conflict_db.py for the import helper.

Schema
------
{
    "full_name":        str,            # required — full legal name
    "passport_numbers": [str, ...],     # list — all passport numbers (multiple passports allowed)
    "emirates_id":      str | null,     # optional
    "nationality":      [str, ...],     # list — supports dual/multiple nationality
    "entity_names":     [str, ...],     # companies or entities associated with the person
    "case_type":        str | null,     # civil, criminal, corporate, etc.
    "opposing_party":   str | null,     # name of opposing party if applicable
    "source_file":      str | null      # origin of the record (filename, import batch, etc.)
}

For a NEW CLIENT being checked (intake), only the first four fields are needed:
{
    "full_name":        "Ahmed Al Marri",
    "passport_numbers": ["P1234567", "P7654321"],
    "emirates_id":      "784-1990-1234567-1",
    "nationality":      ["UAE", "UK"]
}
"""

import re
from typing import Optional


REQUIRED_FIELDS = ["full_name"]
ALL_FIELDS = [
    "full_name",
    "passport_numbers",
    "emirates_id",
    "nationality",
    "entity_names",
    "case_type",
    "opposing_party",
    "source_file",
]


def make_empty_payload() -> dict:
    """Return a blank conflict check payload with safe defaults."""
    return {
        "full_name":        "",
        "passport_numbers": [],
        "emirates_id":      None,
        "nationality":      [],
        "entity_names":     [],
        "case_type":        None,
        "opposing_party":   None,
        "source_file":      None,
    }


def normalise_ocr_output(ocr_raw: dict, source_file: str = None) -> dict:
    """
    Convert raw PaddleOCR output (from passport or Emirates ID) into a
    conflict check payload.

    PaddleOCR returns field labels and values detected from the document.
    This function maps common field names to the canonical schema.

    Args:
        ocr_raw:     Dict of field → value as returned by PaddleOCR processing
        source_file: Optional label for where this record came from

    Returns:
        Conflict check payload dict
    """
    payload = make_empty_payload()
    payload["source_file"] = source_file or "ocr_extraction"

    # Name — common OCR field names
    for key in ("surname_given_names", "full_name", "name", "holder_name"):
        if ocr_raw.get(key):
            payload["full_name"] = _clean_name(ocr_raw[key])
            break

    # Passport number
    for key in ("passport_no", "passport_number", "document_number", "doc_no"):
        if ocr_raw.get(key):
            payload["passport_numbers"] = [_clean_id(ocr_raw[key])]
            break

    # Emirates ID
    for key in ("id_number", "emirates_id", "uid_number"):
        if ocr_raw.get(key):
            payload["emirates_id"] = _clean_id(ocr_raw[key])
            break

    # Nationality — store as list even if single value
    for key in ("nationality", "national"):
        if ocr_raw.get(key):
            nat = ocr_raw[key]
            payload["nationality"] = [nat] if isinstance(nat, str) else list(nat)
            break

    return payload


def normalise_manual_input(form_data: dict) -> dict:
    """
    Convert manual client form input into a conflict check payload.

    Args:
        form_data: Dict from web portal or WhatsApp flow containing client info

    Returns:
        Conflict check payload dict
    """
    payload = make_empty_payload()
    payload["source_file"] = "manual_input"

    payload["full_name"] = _clean_name(form_data.get("full_name", ""))

    # Accept passport_numbers as list or single string
    passports = form_data.get("passport_numbers", [])
    if isinstance(passports, str):
        passports = [passports]
    payload["passport_numbers"] = [_clean_id(p) for p in passports if p]

    if form_data.get("emirates_id"):
        payload["emirates_id"] = _clean_id(form_data["emirates_id"])

    # Nationality as list
    nat = form_data.get("nationality", [])
    if isinstance(nat, str):
        nat = [nat]
    payload["nationality"] = [n.strip() for n in nat if n]

    return payload


def normalise_db_record(json_record: dict) -> dict:
    """
    Normalise a JSON record from the firm's existing conflict DB
    into the canonical conflict check payload shape.

    Use this when importing JSON files via scripts/import_conflict_db.py.

    Args:
        json_record: A single record from the firm's JSON DB file

    Returns:
        Normalised payload ready to be inserted into conflict_index table
    """
    payload = make_empty_payload()

    payload["full_name"]        = _clean_name(json_record.get("full_name", ""))
    payload["emirates_id"]      = _clean_id(json_record.get("emirates_id")) if json_record.get("emirates_id") else None
    payload["case_type"]        = json_record.get("case_type")
    payload["opposing_party"]   = json_record.get("opposing_party")
    payload["source_file"]      = json_record.get("source_file")

    passports = json_record.get("passport_numbers", [])
    if isinstance(passports, str):
        passports = [passports]
    payload["passport_numbers"] = [_clean_id(p) for p in passports if p]

    nat = json_record.get("nationality", [])
    if isinstance(nat, str):
        nat = [nat]
    payload["nationality"] = [n.strip() for n in nat if n]

    entities = json_record.get("entity_names", [])
    if isinstance(entities, str):
        entities = [entities]
    payload["entity_names"] = [e.strip() for e in entities if e]

    return payload


def validate_payload(payload: dict) -> list:
    """
    Validate a conflict check payload. Returns a list of error strings.
    Empty list means valid.
    """
    errors = []
    if not payload.get("full_name"):
        errors.append("full_name is required.")
    if not isinstance(payload.get("passport_numbers", []), list):
        errors.append("passport_numbers must be a list.")
    if not isinstance(payload.get("nationality", []), list):
        errors.append("nationality must be a list.")
    return errors


# ─── Private helpers ──────────────────────────────────────────────────────────

def _clean_name(name: str) -> str:
    """Normalise a name: strip extra whitespace, title-case."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name).strip()


def _clean_id(id_str: str) -> str:
    """Normalise an ID string: uppercase, strip spaces and dashes."""
    if not id_str:
        return ""
    return re.sub(r"[\s\-]", "", str(id_str)).upper()
