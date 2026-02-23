"""
reference.py — Reference ID and portal token generation.

Reference ID format: ITF-YYYY-NNNNN (e.g. ITF-2026-04821)
Portal token:        URL-safe random string, 32 bytes
"""

import secrets
import string
from datetime import datetime, timezone, timedelta
from database import db


def generate_reference_id(firm_id: str) -> str:
    """
    Generate a unique human-readable reference ID for a client.
    Format: ITF-{YEAR}-{5-digit-sequence}
    e.g.  : ITF-2026-04821

    The sequence is padded to 5 digits and counts from 1 within the year.
    Collisions are prevented by checking DB uniqueness and retrying.
    """
    from models import Client

    year = datetime.now(timezone.utc).year
    prefix = f"ITF-{year}-"

    # Find the highest existing sequence for this year
    last = (
        Client.query
        .filter(Client.reference_id.like(f"{prefix}%"))
        .order_by(Client.reference_id.desc())
        .first()
    )

    if last:
        try:
            last_seq = int(last.reference_id.split("-")[-1])
        except (ValueError, IndexError):
            last_seq = 0
        next_seq = last_seq + 1
    else:
        next_seq = 1

    candidate = f"{prefix}{str(next_seq).zfill(5)}"

    # Safety check — if somehow a collision exists, increment further
    while Client.query.filter_by(reference_id=candidate).first():
        next_seq += 1
        candidate = f"{prefix}{str(next_seq).zfill(5)}"

    return candidate


def generate_portal_token() -> str:
    """
    Generate a cryptographically secure portal token.
    Returns a 48-character URL-safe random string.
    """
    return secrets.token_urlsafe(36)  # 36 bytes → ~48 chars URL-safe


def token_expiry(days: int = 30):
    """Return a UTC datetime `days` from now."""
    return datetime.now(timezone.utc) + timedelta(days=days)
