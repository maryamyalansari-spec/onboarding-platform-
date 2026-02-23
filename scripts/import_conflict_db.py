"""
import_conflict_db.py — Import existing firm JSON conflict database into PostgreSQL.

Your existing DB files should be JSON arrays where each object represents
one person/entity to check against. Place your JSON files in the
`conflict_data/` folder and run this script.

Expected JSON record shape (all fields optional except full_name):
[
    {
        "full_name":        "Ahmed Al Marri",
        "passport_numbers": ["P1234567"],
        "emirates_id":      "784-1990-1234567-1",
        "nationality":      ["UAE"],
        "entity_names":     ["Al Marri Holdings LLC"],
        "case_type":        "Civil",
        "opposing_party":   "XYZ Corp",
        "source_file":      "clients_2024.json"
    },
    ...
]

Usage:
    python scripts/import_conflict_db.py --firm-id <firm_id> --file conflict_data/my_db.json
    python scripts/import_conflict_db.py --firm-id <firm_id> --dir  conflict_data/
"""

import sys
import os
import json
import argparse
import uuid as _uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from flask import Flask
from config import get_config
from database import db
from models import ConflictIndex
from utils.conflict_schema import normalise_db_record, validate_payload


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())
    db.init_app(app)
    return app


def import_file(file_path: str, firm_id: str, app) -> tuple[int, int]:
    """
    Import a single JSON file into conflict_index.
    Returns (imported_count, skipped_count).
    """
    with open(file_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        records = [records]

    imported = 0
    skipped = 0
    source_file = os.path.basename(file_path)

    with app.app_context():
        for raw in records:
            payload = normalise_db_record(raw)
            if not payload.get("source_file"):
                payload["source_file"] = source_file

            errors = validate_payload(payload)
            if errors:
                print(f"  [SKIP] {raw.get('full_name', '?')} — {errors}")
                skipped += 1
                continue

            # Check for exact duplicate (same firm + same full_name + same passport)
            existing = ConflictIndex.query.filter_by(
                firm_id=firm_id,
                full_name=payload["full_name"]
            ).first()
            if existing:
                # If passport numbers overlap, skip
                existing_passports = set(existing.passport_numbers or [])
                new_passports = set(payload["passport_numbers"])
                if existing_passports & new_passports:
                    print(f"  [DUP]  {payload['full_name']} — duplicate passport, skipping.")
                    skipped += 1
                    continue

            record = ConflictIndex(
                record_id=str(_uuid.uuid4()),
                firm_id=firm_id,
                full_name=payload["full_name"],
                passport_numbers=payload["passport_numbers"],
                emirates_id=payload["emirates_id"],
                nationality=payload["nationality"],
                entity_names=payload["entity_names"],
                case_type=payload["case_type"],
                opposing_party=payload["opposing_party"],
                source_file=payload["source_file"],
                # name_embedding is generated separately by scripts/generate_embeddings.py
                # once OpenAI API key is configured (Step 12)
            )
            db.session.add(record)
            imported += 1

        db.session.commit()

    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description="Import conflict DB JSON files")
    parser.add_argument("--firm-id", required=True, help="Target firm_id in the database")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Path to a single JSON file")
    group.add_argument("--dir",  help="Path to a directory of JSON files")
    args = parser.parse_args()

    app = create_app()

    files = []
    if args.file:
        files = [args.file]
    elif args.dir:
        files = [
            os.path.join(args.dir, f)
            for f in os.listdir(args.dir)
            if f.endswith(".json")
        ]

    if not files:
        print("No JSON files found.")
        sys.exit(1)

    total_imported = 0
    total_skipped = 0

    for file_path in sorted(files):
        print(f"\n[IMPORT] {file_path}")
        imp, skip = import_file(file_path, args.firm_id, app)
        print(f"  Imported: {imp} | Skipped: {skip}")
        total_imported += imp
        total_skipped += skip

    print(f"\n{'='*50}")
    print(f" Total imported: {total_imported}")
    print(f" Total skipped:  {total_skipped}")
    print(f"{'='*50}")
    print("\nNext: run scripts/generate_embeddings.py to generate name vectors")
    print("for soft-match conflict checking (requires OpenAI API key).")


if __name__ == "__main__":
    main()
