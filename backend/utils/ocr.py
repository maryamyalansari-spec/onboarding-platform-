"""
ocr.py — PaddleOCR wrapper for passport and Emirates ID extraction.

Strategy
--------
1. Preferred: MRZ (Machine Readable Zone) parsing — two 44-char lines on
   the bottom of every ICAO-9303 compliant passport.  Highly reliable.
2. Fallback: Regex / keyword scanning of the full OCR text output for
   common field labels (DATE OF BIRTH, NATIONALITY, PASSPORT NO, etc.).

All results are returned as a plain dict with consistent field names that
map directly to normalise_ocr_output() in utils/conflict_schema.py.
"""

import re
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Lazy PaddleOCR instance ───────────────────────────────────────────────────
_ocr_instance = None

def _get_ocr():
    """Lazily initialise PaddleOCR (heavy import, done only once)."""
    global _ocr_instance
    if _ocr_instance is None:
        try:
            from paddleocr import PaddleOCR  # noqa: F401
            _ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="en",
                use_gpu=False,
                show_log=False,
                det_db_box_thresh=0.3,
            )
            logger.info("PaddleOCR initialised.")
        except ImportError:
            logger.error("PaddleOCR not installed. Run: pip install paddleocr paddlepaddle")
            raise
    return _ocr_instance


# ════════════════════════════════════════════════════════════
#  Public API
# ════════════════════════════════════════════════════════════

def extract_text_blocks(file_path: str) -> list[str]:
    """
    Run PaddleOCR on an image or PDF and return all detected text strings
    with confidence >= 0.5, in top-to-bottom order.

    Args:
        file_path: Absolute path to the image or single-page PDF.

    Returns:
        List of text strings (no bounding box info).
    """
    ocr = _get_ocr()
    result = ocr.ocr(file_path, cls=True)

    texts = []
    if result and result[0]:
        for block in result:
            if block is None:
                continue
            for line in block:
                text, confidence = line[1]
                if confidence >= 0.5 and text.strip():
                    texts.append(text.strip())
    return texts


def extract_passport_fields(texts: list[str]) -> dict:
    """
    Parse a flat list of OCR text blocks from a passport image and return
    a structured dict of extracted fields.

    Field names match what normalise_ocr_output() expects:
        full_name, passport_number, nationality,
        date_of_birth, expiry_date, gender, issuing_country
    """
    result = {
        "full_name":       None,
        "passport_number": None,
        "nationality":     None,
        "date_of_birth":   None,
        "expiry_date":     None,
        "gender":          None,
        "issuing_country": None,
        "mrz_parsed":      False,
    }

    # ── 1. Try MRZ first ──────────────────────────────────────
    mrz = _parse_mrz(texts)
    if mrz:
        result.update(mrz)
        result["mrz_parsed"] = True
        return result

    # ── 2. Regex / keyword fallback ───────────────────────────
    joined = " ".join(texts).upper()

    # Passport number — ICAO format: 1-2 uppercase letters + 6-8 digits
    pn_match = re.search(r'\b([A-Z]{1,2}[0-9]{6,8})\b', joined)
    if pn_match:
        result["passport_number"] = pn_match.group(1)

    # Name — look for SURNAME / GIVEN NAME labels
    result["full_name"] = _extract_labeled_field(
        texts,
        labels=["SURNAME", "LAST NAME", "FAMILY NAME",
                "GIVEN NAMES", "FIRST NAME", "NAME"],
        join_adjacent=True,
    )

    # Nationality — ISO 3-letter or common names
    nat = _extract_labeled_field(texts, labels=["NATIONALITY", "NATIONALITÉ"])
    if nat:
        result["nationality"] = nat

    # DOB
    dob = _extract_labeled_field(texts, labels=["DATE OF BIRTH", "BIRTH DATE", "DOB", "DATE DE NAISSANCE"])
    if dob:
        result["date_of_birth"] = _normalise_date(dob)

    # Expiry
    exp = _extract_labeled_field(texts, labels=["DATE OF EXPIRY", "EXPIRY DATE", "EXPIRATION DATE"])
    if exp:
        result["expiry_date"] = _normalise_date(exp)

    # Gender
    for txt in texts:
        if re.fullmatch(r'[MF]', txt.strip().upper()):
            result["gender"] = txt.strip().upper()
            break

    return result


def extract_emirates_id_fields(texts: list[str]) -> dict:
    """
    Parse OCR text blocks from a UAE Emirates ID card image.

    Returns:
        Dict with: id_number, full_name, nationality, date_of_birth, expiry_date
    """
    result = {
        "id_number":    None,
        "full_name":    None,
        "nationality":  None,
        "date_of_birth": None,
        "expiry_date":  None,
    }

    joined_raw = " ".join(texts)
    joined     = joined_raw.upper()

    # ── Emirates ID number ─────────────────────────────────────
    eid_match = re.search(r'784[-\s]?(\d{4})[-\s]?(\d{7})[-\s]?(\d{1})', joined_raw)
    if eid_match:
        result["id_number"] = f"784-{eid_match.group(1)}-{eid_match.group(2)}-{eid_match.group(3)}"
    else:
        # Try without separators
        eid_match2 = re.search(r'784(\d{13})', joined_raw)
        if eid_match2:
            raw = "784" + eid_match2.group(1)
            result["id_number"] = f"784-{raw[3:7]}-{raw[7:14]}-{raw[14]}"

    # ── Name ──────────────────────────────────────────────────
    result["full_name"] = _extract_labeled_field(
        texts,
        labels=["NAME", "FULL NAME", "HOLDER", "الاسم"],
    )

    # ── Nationality ───────────────────────────────────────────
    nat = _extract_labeled_field(texts, labels=["NATIONALITY", "الجنسية"])
    if nat:
        result["nationality"] = nat

    # ── Dates ─────────────────────────────────────────────────
    # DOB
    dob = _extract_labeled_field(texts, labels=["DATE OF BIRTH", "BIRTH", "تاريخ الميلاد"])
    if dob:
        result["date_of_birth"] = _normalise_date(dob)

    # Expiry
    exp = _extract_labeled_field(texts, labels=["EXPIRY", "EXPIRATION", "DATE OF EXPIRY", "تاريخ الانتهاء"])
    if exp:
        result["expiry_date"] = _normalise_date(exp)

    return result


# ════════════════════════════════════════════════════════════
#  MRZ parsing
# ════════════════════════════════════════════════════════════

def _parse_mrz(texts: list[str]) -> Optional[dict]:
    """
    Locate two consecutive 44-character MRZ lines and extract passport fields.
    Returns None if no valid MRZ found.
    """
    # Clean: remove spaces, keep only MRZ-valid chars
    candidates = []
    for t in texts:
        clean = re.sub(r'\s', '', t.upper())
        # Allow slight length variation (43-45) and clean to pure MRZ chars
        clean = re.sub(r'[^A-Z0-9<]', '<', clean)
        if 43 <= len(clean) <= 45:
            # Pad or trim to exactly 44
            if len(clean) < 44:
                clean = clean.ljust(44, '<')
            else:
                clean = clean[:44]
            candidates.append(clean)

    if len(candidates) < 2:
        return None

    # Find consecutive valid pair — line1 starts with P, line2 looks numeric
    line1 = None
    line2 = None
    for i, c in enumerate(candidates):
        if c[0] == 'P' and i + 1 < len(candidates):
            next_c = candidates[i + 1]
            # Line 2 should have numeric chars at positions 13-18 (DOB)
            if re.match(r'^[A-Z0-9<]{9}[0-9][A-Z<]{3}[0-9]{6}', next_c):
                line1 = c
                line2 = next_c
                break

    if not line1 or not line2:
        return None

    try:
        # Parse line 1
        doc_type_country = line1[0:5]         # P<CCCor P<<CCC
        issuing_country  = line1[2:5].strip('<')
        name_field       = line1[5:44]
        if '<<' in name_field:
            surname_raw, given_raw = name_field.split('<<', 1)
            surname     = surname_raw.replace('<', ' ').strip()
            given_names = given_raw.rstrip('<').replace('<', ' ').strip()
            full_name   = f"{given_names} {surname}".strip()
        else:
            full_name = name_field.replace('<', ' ').strip()

        # Parse line 2
        passport_number = line2[0:9].rstrip('<')
        # check_digit_pn = line2[9]
        nationality     = line2[10:13].strip('<')
        dob_raw         = line2[13:19]   # YYMMDD
        # check_dob      = line2[19]
        sex             = line2[20]
        expiry_raw      = line2[21:27]   # YYMMDD
        # check_exp      = line2[27]

        dob    = _mrz_date(dob_raw, is_birth=True)
        expiry = _mrz_date(expiry_raw, is_birth=False)

        return {
            "full_name":       full_name,
            "passport_number": passport_number,
            "nationality":     nationality if len(nationality) == 3 else None,
            "date_of_birth":   dob,
            "expiry_date":     expiry,
            "gender":          sex if sex in ("M", "F") else None,
            "issuing_country": issuing_country if len(issuing_country) <= 3 else None,
        }
    except (IndexError, ValueError) as e:
        logger.debug(f"MRZ parse error: {e}")
        return None


def _mrz_date(yymmdd: str, is_birth: bool = False) -> Optional[str]:
    """Convert MRZ YYMMDD to ISO date string YYYY-MM-DD."""
    if not re.match(r'^\d{6}$', yymmdd):
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    current_year = datetime.now().year % 100
    if is_birth:
        # If YY > current year + 1, assume 1900s
        year = (1900 + yy) if yy > (current_year + 1) else (2000 + yy)
    else:
        # Expiry dates are always in the future — always 20xx
        year = 2000 + yy
    try:
        return f"{year:04d}-{mm:02d}-{dd:02d}"
    except ValueError:
        return None


# ════════════════════════════════════════════════════════════
#  Keyword / label extraction helpers
# ════════════════════════════════════════════════════════════

def _extract_labeled_field(
    texts: list[str],
    labels: list[str],
    join_adjacent: bool = False,
) -> Optional[str]:
    """
    Find a value that appears on the same line as a label, or on the very
    next line.  E.g. texts = ["NATIONALITY", "UNITED ARAB EMIRATES"] → returns
    "UNITED ARAB EMIRATES".
    """
    labels_upper = [l.upper() for l in labels]
    for i, text in enumerate(texts):
        t_upper = text.upper()
        # Check if this line IS a label (or starts with one)
        for label in labels_upper:
            if label in t_upper:
                # Value might be in the same line after a colon / slash
                after = re.split(r'[:/]', t_upper, maxsplit=1)
                if len(after) > 1 and after[1].strip():
                    return after[1].strip().title()
                # Or on the next line
                if i + 1 < len(texts) and texts[i + 1].strip():
                    val = texts[i + 1].strip()
                    if join_adjacent and i + 2 < len(texts):
                        next2 = texts[i + 2].strip()
                        # If next2 also looks like a name part, concatenate
                        if re.match(r'^[A-Za-z\s\-]+$', next2):
                            val = f"{val} {next2}"
                    return val.title()
    return None


def _normalise_date(date_str: str) -> Optional[str]:
    """
    Try to parse a date string into ISO format (YYYY-MM-DD).
    Handles: DD/MM/YYYY, DD-MM-YYYY, DDMMYYYY, DD MMM YYYY, etc.
    """
    if not date_str:
        return None
    s = date_str.strip().upper()

    # Already ISO
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s

    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d %B %Y",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Compact DDMMYYYY
    if re.match(r'^\d{8}$', s):
        try:
            return datetime.strptime(s, "%d%m%Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None
