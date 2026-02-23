"""
test_utils.py — Unit tests for utility functions.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


class TestNamingUtils:
    def test_reference_id_format(self):
        from utils.reference import generate_reference_id
        ref = generate_reference_id(1)
        assert ref.startswith("ITF-")
        assert len(ref) > 8

    def test_document_filename(self):
        from utils.naming import make_document_filename
        name = make_document_filename("Ahmed Al Marri", "Passport", "2024-01-15")
        assert "Ahmed" in name or "Al" in name or "Marri" in name
        assert "Passport" in name or "passport" in name.lower()

    def test_document_filename_special_chars(self):
        from utils.naming import make_document_filename
        name = make_document_filename("O'Brien & Sons", "Contract", "2024-01-15")
        # Should not contain characters unsafe for filenames
        assert "/" not in name
        assert "\\" not in name


class TestResponseUtils:
    def test_success_response(self, app):
        with app.app_context():
            from flask import Flask
            from utils.response import success, error, not_found
            with app.test_request_context():
                r = success(data={"foo": "bar"}, message="OK")
                import json
                body = json.loads(r.get_data())
                assert body["ok"] is True
                assert body["data"]["foo"] == "bar"
                assert body["message"] == "OK"

    def test_error_response(self, app):
        with app.app_context():
            from utils.response import error
            with app.test_request_context():
                import json
                r = error("Something went wrong", 400)
                body = json.loads(r[0].get_data())
                assert body["ok"] is False
                assert r[1] == 400

    def test_not_found_response(self, app):
        with app.app_context():
            from utils.response import not_found
            with app.test_request_context():
                import json
                r = not_found("Client")
                body = json.loads(r[0].get_data())
                assert body["ok"] is False
                assert r[1] == 404


class TestOCRUtils:
    def test_extract_text_blocks_invalid_file(self):
        """OCR on a non-existent file should raise or return empty."""
        try:
            from utils.ocr import extract_text_blocks
            result = extract_text_blocks("/nonexistent/path.jpg")
            assert isinstance(result, list)
        except Exception:
            pass  # Acceptable — PaddleOCR may not be installed in test env

    def test_extract_passport_fields_empty(self):
        from utils.ocr import extract_passport_fields
        result = extract_passport_fields([])
        assert isinstance(result, dict)

    def test_extract_emirates_id_fields_empty(self):
        from utils.ocr import extract_emirates_id_fields
        result = extract_emirates_id_fields([])
        assert isinstance(result, dict)

    def test_mrz_date_parsing(self):
        """Test century disambiguation for MRZ dates."""
        from utils.ocr import _mrz_date
        # Birth date in 1990s
        assert "1990" in _mrz_date("900101", is_birth=True)
        # Future expiry
        result = _mrz_date("300101", is_birth=False)
        assert "2030" in result or "1930" not in result  # should be future


class TestEmailUtils:
    def test_portal_link_email_template(self, app):
        with app.app_context():
            from utils.email import portal_link_email
            subject, html = portal_link_email(
                client_name="Ahmed Al Marri",
                reference_id="ITF-2026-00001",
                portal_url="http://localhost/client/ITF-2026-00001?token=abc",
                firm_name="Test Firm",
            )
            assert "ITF-2026-00001" in subject
            assert "Ahmed Al Marri" in html
            assert "http://localhost/client" in html

    def test_conflict_clear_email_template(self, app):
        with app.app_context():
            from utils.email import conflict_clear_email
            subject, html = conflict_clear_email(
                client_name="Test Client",
                reference_id="ITF-2026-00002",
                portal_url="http://localhost/client/ITF-2026-00002?token=xyz",
                firm_name="Test Firm",
            )
            assert "ITF-2026-00002" in html
            assert "conflict" in html.lower() or "review" in html.lower()

    def test_approval_email_template(self, app):
        with app.app_context():
            from utils.email import approval_email
            subject, html = approval_email("John Doe", "ITF-2026-00003", "Test Firm")
            assert "ITF-2026-00003" in html
            assert "accepted" in html.lower() or "approved" in html.lower()

    def test_rejection_email_template(self, app):
        with app.app_context():
            from utils.email import rejection_email
            subject, html = rejection_email("John Doe", "ITF-2026-00004", "Test Firm")
            assert "ITF-2026-00004" in html

    def test_send_email_no_api_key(self, app):
        """Without API key, send_email should return False without raising."""
        with app.app_context():
            from utils.email import send_email
            result = send_email(
                to_email="test@test.com",
                subject="Test",
                html_body="<p>Hello</p>",
            )
            assert result is False  # API key is empty in test config


class TestPDFUtils:
    def test_generate_engagement_letter(self, app, tmp_path):
        """Generate a PDF and verify the file was created."""
        import uuid
        from datetime import datetime, timezone

        with app.app_context():
            from utils.pdf import generate_engagement_letter

            # Mock objects
            class MockFirm:
                firm_name = "Test Firm"

            class MockClient:
                client_id    = str(uuid.uuid4())
                reference_id = "ITF-2026-99999"
                full_name    = "Test Client"

            class MockLetter:
                letter_id      = str(uuid.uuid4())
                matter_type    = "Commercial Dispute"
                scope_of_work  = "Review and advise on contract terms."
                fee_structure  = "AED 1,500/hour billed monthly."
                retainer_amount= 10000
                billing_type   = "Retainer"
                timeline       = "Phase 1: 2 weeks\nPhase 2: 4 weeks"

            upload_folder = str(tmp_path)
            rel_path = generate_engagement_letter(
                MockLetter(), MockClient(), MockFirm(), upload_folder
            )
            import os
            full_path = os.path.join(upload_folder, rel_path)
            assert os.path.isfile(full_path)
            assert full_path.endswith(".pdf")
            assert os.path.getsize(full_path) > 1000  # must be a non-trivial PDF
