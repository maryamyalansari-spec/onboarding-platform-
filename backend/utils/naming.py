"""
naming.py — Document file naming conventions.

All documents saved as: ClientName_DocumentType_YYYY-MM-DD
Spaces replaced with underscores.
Example: Ahmed_AlMarri_BusinessLicense_2026-02-23
"""

import re
from datetime import date


def make_document_filename(client_name: str, document_type: str, extension: str, upload_date: date = None) -> str:
    """
    Generate a standardised document filename.

    Args:
        client_name:   Full client name, e.g. "Ahmed Al Marri"
        document_type: Category label, e.g. "Business License"
        extension:     File extension without dot, e.g. "pdf"
        upload_date:   Date of upload (defaults to today)

    Returns:
        e.g. "Ahmed_AlMarri_BusinessLicense_2026-02-23.pdf"
    """
    if upload_date is None:
        upload_date = date.today()

    # Sanitise name: remove non-alphanumeric chars except spaces, then replace spaces
    clean_name = re.sub(r"[^\w\s]", "", client_name).strip()
    clean_name = re.sub(r"\s+", "_", clean_name)

    # Sanitise document type
    clean_type = re.sub(r"[^\w\s]", "", document_type).strip()
    clean_type = re.sub(r"\s+", "", clean_type)   # no separator for type

    date_str = upload_date.strftime("%Y-%m-%d")
    ext = extension.lstrip(".")

    return f"{clean_name}_{clean_type}_{date_str}.{ext}"


def make_audio_filename(client_id: str, sequence_number: int, extension: str) -> str:
    """
    Generate a standardised audio filename for statement voice notes.
    e.g. "stmt_abc123_1_2026-02-23.webm"
    """
    date_str = date.today().strftime("%Y-%m-%d")
    ext = extension.lstrip(".")
    return f"stmt_{client_id[:8]}_{sequence_number}_{date_str}.{ext}"
