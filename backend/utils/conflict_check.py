"""
conflict_check.py — 3-tier conflict of interest check engine.

Tiers
─────
1. Exact   (score 90–100) — passport number or Emirates ID exact match
2. Strong  (score 60–85)  — name fuzzy-match (≥ 85 %) AND nationality overlap
3. Soft    (score 20–59)  — pgvector cosine similarity on name embedding > 0.85

Scoring
───────
Tier 1 passport match   → 95
Tier 1 Emirates ID match → 90
Tier 2 name ≥ 92 % + nat → 82
Tier 2 name ≥ 85 % + nat → 72
Tier 2 name ≥ 92 % only  → 65
Tier 3 cosine ≥ 0.97     → 55
Tier 3 cosine ≥ 0.93     → 45
Tier 3 cosine ≥ 0.87     → 30
Tier 3 cosine ≥ 0.85     → 22

Admin notification threshold: score ≥ 50 → sets client status to manual_review.
Clean (score < 50)           → sets client status to context_collection.
"""

import logging
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import text as sql_text
from database import db
from models import (
    Client, ClientStatus, ConflictIndex, ConflictResult,
    MatchType, ConflictDecision, Passport, EmiratesID,
)
from utils.conflict_schema import normalise_ocr_output, normalise_manual_input

logger = logging.getLogger(__name__)

# Score at which a result requires admin review
REVIEW_THRESHOLD = 50


# ════════════════════════════════════════════════════════════
#  Main entry point
# ════════════════════════════════════════════════════════════

def run_conflict_check(client_id: str) -> dict:
    """
    Run the full 3-tier conflict check for a client.

    Builds the conflict payload from OCR output (or falls back to manual
    input data), runs all three tiers in order of priority, stores the
    best result in conflict_results, and updates the client status.

    Returns:
        Dict with keys: conflict_id, match_type, confidence_score,
        decision, matched_record_id.
    """
    client = Client.query.get(client_id)
    if not client:
        raise ValueError(f"Client {client_id} not found.")

    firm_id = client.firm_id

    # ── Build the payload from available data ─────────────────
    payload = _build_payload(client)
    logger.info(f"[Conflict] Running check for {client_id}: {payload}")

    # ── Generate name embedding for tier 3 ────────────────────
    embedding = None
    if payload.get("full_name"):
        try:
            from routes.ai import generate_embedding
            embedding = generate_embedding(payload["full_name"])
        except (NotImplementedError, Exception) as e:
            logger.warning(f"[Conflict] Embedding unavailable: {e}. Tier 3 will be skipped.")

    # ── Tier 1: Exact ID match ────────────────────────────────
    best = _tier1_exact(payload, firm_id)
    if not best:
        # ── Tier 2: Strong name + nationality ─────────────────
        best = _tier2_strong(payload, firm_id)
    if not best and embedding:
        # ── Tier 3: Soft vector similarity ────────────────────
        best = _tier3_soft(embedding, firm_id)

    # ── Default: no conflict ──────────────────────────────────
    if not best:
        best = {
            "match_type":       MatchType.none,
            "confidence_score": 0.0,
            "matched_record_id": None,
        }

    logger.info(f"[Conflict] Result for {client_id}: {best['match_type'].value} score={best['confidence_score']}")

    # ── Store result ──────────────────────────────────────────
    import uuid
    conflict_result = ConflictResult(
        conflict_id       = str(uuid.uuid4()),
        client_id         = client_id,
        match_type        = best["match_type"],
        matched_record_id = best.get("matched_record_id"),
        confidence_score  = best["confidence_score"],
        decision          = ConflictDecision.pending,
    )
    db.session.add(conflict_result)

    # ── Update client status based on score ───────────────────
    score = float(best["confidence_score"])
    if score >= REVIEW_THRESHOLD:
        # Needs human review — flag as manual_review
        client.status = ClientStatus.manual_review
        logger.info(f"[Conflict] Client {client_id} flagged for manual review (score={score}).")
    else:
        # Clean — advance to context collection (statement step)
        if client.status in (ClientStatus.conflict_check, ClientStatus.id_uploaded):
            client.status = ClientStatus.context_collection
            logger.info(f"[Conflict] Client {client_id} cleared, status → context_collection.")

    db.session.commit()

    # ── Also store embedding for future lookups ───────────────
    if embedding and payload.get("passport_numbers"):
        _upsert_conflict_index(payload, embedding, firm_id)

    return {
        "conflict_id":       conflict_result.conflict_id,
        "match_type":        best["match_type"].value,
        "confidence_score":  float(best["confidence_score"]),
        "decision":          ConflictDecision.pending.value,
        "matched_record_id": best.get("matched_record_id"),
    }


# ════════════════════════════════════════════════════════════
#  Tier 1 — Exact ID match
# ════════════════════════════════════════════════════════════

def _tier1_exact(payload: dict, firm_id: str) -> Optional[dict]:
    """Check for exact passport number or Emirates ID match."""
    passport_numbers = payload.get("passport_numbers") or []
    emirates_id      = payload.get("emirates_id")

    if not passport_numbers and not emirates_id:
        return None

    # Query using PostgreSQL array overlap operator &&
    if passport_numbers:
        # Build parameterised array literal
        arr_literal = "{" + ",".join(passport_numbers) + "}"
        rows = db.session.execute(sql_text(
            """
            SELECT record_id, full_name, passport_numbers, nationality,
                   case_type, opposing_party, emirates_id
            FROM conflict_index
            WHERE firm_id = :firm_id
              AND passport_numbers && CAST(:arr AS varchar[])
            LIMIT 5
            """
        ), {"firm_id": firm_id, "arr": arr_literal}).fetchall()

        if rows:
            row = rows[0]
            logger.info(f"[Conflict T1] Passport match: {row.full_name}")
            return {
                "match_type":        MatchType.exact,
                "confidence_score":  95.0,
                "matched_record_id": row.record_id,
            }

    # Emirates ID exact match
    if emirates_id:
        row = db.session.execute(sql_text(
            """
            SELECT record_id, full_name
            FROM conflict_index
            WHERE firm_id = :firm_id
              AND emirates_id = :eid
            LIMIT 1
            """
        ), {"firm_id": firm_id, "eid": emirates_id}).fetchone()

        if row:
            logger.info(f"[Conflict T1] Emirates ID match: {row.full_name}")
            return {
                "match_type":        MatchType.exact,
                "confidence_score":  90.0,
                "matched_record_id": row.record_id,
            }

    return None


# ════════════════════════════════════════════════════════════
#  Tier 2 — Strong name + nationality match
# ════════════════════════════════════════════════════════════

def _tier2_strong(payload: dict, firm_id: str) -> Optional[dict]:
    """
    Fetch all names in the firm's conflict index and fuzzy-match
    against the incoming name using difflib SequenceMatcher.
    Also checks nationality overlap for higher confidence.
    """
    query_name = (payload.get("full_name") or "").lower().strip()
    if not query_name:
        return None

    query_nats = {n.upper() for n in (payload.get("nationality") or [])}

    rows = db.session.execute(sql_text(
        """
        SELECT record_id, full_name, nationality, case_type, opposing_party
        FROM conflict_index
        WHERE firm_id = :firm_id
        """
    ), {"firm_id": firm_id}).fetchall()

    best_score  = 0.0
    best_record = None

    for row in rows:
        db_name = (row.full_name or "").lower().strip()
        if not db_name:
            continue

        ratio = SequenceMatcher(None, query_name, db_name).ratio()
        if ratio < 0.85:
            continue

        # Check nationality overlap
        db_nats = {n.upper() for n in (row.nationality or [])}
        nat_overlap = bool(query_nats & db_nats)

        # Assign score
        if ratio >= 0.92 and nat_overlap:
            score = 82.0
        elif ratio >= 0.85 and nat_overlap:
            score = 72.0
        elif ratio >= 0.92:
            score = 65.0
        else:
            score = 60.0

        if score > best_score:
            best_score  = score
            best_record = row.record_id
            logger.info(f"[Conflict T2] Name match '{row.full_name}' ratio={ratio:.2f} score={score}")

    if best_record:
        return {
            "match_type":        MatchType.strong,
            "confidence_score":  best_score,
            "matched_record_id": best_record,
        }
    return None


# ════════════════════════════════════════════════════════════
#  Tier 3 — Soft pgvector cosine similarity
# ════════════════════════════════════════════════════════════

def _tier3_soft(embedding: list, firm_id: str) -> Optional[dict]:
    """
    Use pgvector cosine distance (<=> operator) to find soft name matches.
    1 - cosine_distance = cosine_similarity.
    """
    # Format embedding for pgvector: '[0.1, 0.2, ...]'
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    try:
        rows = db.session.execute(sql_text(
            """
            SELECT record_id, full_name,
                   1 - (name_embedding <=> CAST(:vec AS vector)) AS similarity
            FROM conflict_index
            WHERE firm_id = :firm_id
              AND name_embedding IS NOT NULL
            ORDER BY name_embedding <=> CAST(:vec AS vector)
            LIMIT 10
            """
        ), {"firm_id": firm_id, "vec": vec_str}).fetchall()
    except Exception as e:
        logger.warning(f"[Conflict T3] pgvector query failed: {e}")
        return None

    for row in rows:
        sim = float(row.similarity)
        if sim < 0.85:
            break   # results are ordered by distance — stop at first below threshold

        if sim >= 0.97:
            score = 55.0
        elif sim >= 0.93:
            score = 45.0
        elif sim >= 0.87:
            score = 30.0
        else:
            score = 22.0

        logger.info(f"[Conflict T3] Vector match '{row.full_name}' sim={sim:.3f} score={score}")
        return {
            "match_type":        MatchType.soft,
            "confidence_score":  score,
            "matched_record_id": row.record_id,
        }

    return None


# ════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════

def _build_payload(client: Client) -> dict:
    """
    Build a conflict check payload for a client by collecting OCR
    output from all uploaded passports and Emirates IDs.
    Falls back to manual input (client.full_name) if OCR hasn't run.
    """
    passport_numbers = []
    nationalities    = []
    full_name        = None
    emirates_id      = None

    # Collect from all passports (OCR data if available)
    for p in client.passports:
        if p.passport_number:
            passport_numbers.append(p.passport_number)
        if p.nationality:
            nationalities.append(p.nationality)
        if p.ocr_raw and p.ocr_raw.get("full_name") and not full_name:
            full_name = p.ocr_raw["full_name"]

    # Emirates ID
    for eid in client.emirates_ids:
        if eid.id_number:
            emirates_id = eid.id_number
            break

    # Fallback: use client.full_name if OCR didn't get a name
    if not full_name:
        full_name = client.full_name

    return {
        "full_name":        full_name,
        "passport_numbers": list(set(passport_numbers)),
        "emirates_id":      emirates_id,
        "nationality":      list(set(nationalities)),
    }


def _upsert_conflict_index(payload: dict, embedding: list, firm_id: str):
    """
    Add or update this client's data in the conflict_index so future
    clients can be checked against them.  Only stores if passports present.
    """
    if not payload.get("passport_numbers") or not payload.get("full_name"):
        return

    # Check if already exists by passport overlap
    arr_literal = "{" + ",".join(payload["passport_numbers"]) + "}"
    existing = db.session.execute(sql_text(
        """
        SELECT record_id FROM conflict_index
        WHERE firm_id = :firm_id
          AND passport_numbers && CAST(:arr AS varchar[])
        LIMIT 1
        """
    ), {"firm_id": firm_id, "arr": arr_literal}).fetchone()

    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"

    if existing:
        db.session.execute(sql_text(
            """
            UPDATE conflict_index
            SET full_name = :name,
                name_embedding = CAST(:vec AS vector),
                nationality = CAST(:nat AS varchar[])
            WHERE record_id = :rid
            """
        ), {
            "name": payload["full_name"],
            "vec":  vec_str,
            "nat":  "{" + ",".join(payload.get("nationality", [])) + "}",
            "rid":  existing.record_id,
        })
    else:
        import uuid
        db.session.execute(sql_text(
            """
            INSERT INTO conflict_index
                (record_id, firm_id, full_name, name_embedding,
                 passport_numbers, emirates_id, nationality, source_file)
            VALUES
                (:rid, :firm_id, :name, CAST(:vec AS vector),
                 CAST(:passports AS varchar[]), :eid,
                 CAST(:nat AS varchar[]), 'intake')
            """
        ), {
            "rid":      str(uuid.uuid4()),
            "firm_id":  firm_id,
            "name":     payload["full_name"],
            "vec":      vec_str,
            "passports": "{" + ",".join(payload["passport_numbers"]) + "}",
            "eid":      payload.get("emirates_id"),
            "nat":      "{" + ",".join(payload.get("nationality", [])) + "}",
        })

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[Conflict] Failed to upsert conflict index: {e}")
