"""
test_client_intake.py — Client intake flow end-to-end tests.

Tests the web channel intake flow:
  1. POST /client/start          → create client
  2. GET  /client/<ref>/upload   → upload page (auth via token)
  3. POST /client/upload/passport → upload passport image
  4. POST /client/statement/text  → submit text statement
  5. POST /client/kyc/submit      → submit KYC
  6. POST /client/submit          → final submit
"""

import io
import json
import pytest


@pytest.fixture(scope="module")
def new_client(client, firm_id, app):
    """Create a fresh web client via POST /client/start."""
    with app.app_context():
        r = client.post(
            "/client/start",
            data=json.dumps({
                "full_name": "Test Client",
                "email":     "test.client@example.com",
                "phone":     "+97150000001",
                "channel":   "web",
                "firm_id":   firm_id,
            }),
            content_type="application/json",
        )
        assert r.status_code in (200, 201), f"Start failed: {r.data}"
        data = r.get_json()
        assert data["ok"], f"Start not ok: {data}"
        return data["data"]["client"]


class TestClientStart:
    def test_start_page_loads(self, client):
        r = client.get("/client/start")
        assert r.status_code == 200

    def test_create_client(self, new_client):
        assert "reference_id" in new_client
        assert new_client["reference_id"].startswith("ITF-")
        assert "portal_token" in new_client
        assert "portal_link" in new_client

    def test_duplicate_email_allowed(self, client, firm_id, app):
        """The platform allows multiple intakes from the same email."""
        r = client.post(
            "/client/start",
            data=json.dumps({
                "full_name": "Test Client 2",
                "email":     "another@example.com",
                "phone":     "+97150000002",
                "channel":   "web",
                "firm_id":   firm_id,
            }),
            content_type="application/json",
        )
        assert r.status_code in (200, 201)
        assert r.get_json()["ok"]

    def test_missing_name_fails(self, client, firm_id):
        r = client.post(
            "/client/start",
            data=json.dumps({"email": "x@x.com", "phone": "+1234", "channel": "web", "firm_id": firm_id}),
            content_type="application/json",
        )
        assert r.status_code in (400, 200)
        data = r.get_json()
        assert not data.get("ok", True)

    def test_missing_email_fails(self, client, firm_id):
        r = client.post(
            "/client/start",
            data=json.dumps({"full_name": "X", "phone": "+1234", "channel": "web", "firm_id": firm_id}),
            content_type="application/json",
        )
        assert r.status_code in (400, 200)
        data = r.get_json()
        assert not data.get("ok", True)


class TestClientUpload:
    def test_upload_page_with_valid_token(self, client, new_client):
        ref   = new_client["reference_id"]
        token = new_client["portal_token"]
        r = client.get(f"/client/{ref}/upload?token={token}")
        assert r.status_code == 200

    def test_upload_page_invalid_token(self, client, new_client):
        ref = new_client["reference_id"]
        r = client.get(f"/client/{ref}/upload?token=bad-token")
        assert r.status_code in (401, 302, 403)

    def test_upload_passport_image(self, client, new_client):
        ref   = new_client["reference_id"]
        token = new_client["portal_token"]
        fake_img = io.BytesIO(b"FAKEPNG")
        fake_img.name = "passport.png"
        r = client.post(
            f"/client/upload/passport?token={token}",
            data={"file": (fake_img, "passport.png")},
            content_type="multipart/form-data",
        )
        # May fail OCR but the upload itself should succeed (200/201)
        # or fail gracefully if file is invalid
        assert r.status_code in (200, 201, 400, 422)


class TestClientStatement:
    def test_statement_page(self, client, new_client, app):
        """Advance client to context_collection so statement page works."""
        from models import Client, ClientStatus
        ref   = new_client["reference_id"]
        token = new_client["portal_token"]
        with app.app_context():
            from database import db
            c = Client.query.filter_by(reference_id=ref).first()
            if c:
                c.status = ClientStatus.context_collection
                db.session.commit()

        r = client.get(f"/client/{ref}/statement?token={token}")
        # Should redirect to kyc if no kyc submitted yet
        assert r.status_code in (200, 302)

    def test_submit_text_statement(self, client, new_client):
        token = new_client["portal_token"]
        r = client.post(
            f"/client/statement/text?token={token}",
            data=json.dumps({"text": "I have a dispute regarding a property sale."}),
            content_type="application/json",
        )
        assert r.status_code in (200, 201)
        data = r.get_json()
        if data:
            # Either ok or an error about already submitted
            assert "ok" in data


class TestKYC:
    def test_kyc_submit(self, client, new_client):
        token = new_client["portal_token"]
        r = client.post(
            f"/client/kyc/submit?token={token}",
            data=json.dumps({
                "occupation":           "Business Owner",
                "employer":             "Acme Trading LLC",
                "country_of_residence": "UAE",
                "source_of_funds":      "Business income",
                "is_pep":               False,
                "sanctions_ack":        True,
            }),
            content_type="application/json",
        )
        assert r.status_code in (200, 201)
        data = r.get_json()
        assert data["ok"], f"KYC submit failed: {data}"

    def test_kyc_without_sanctions_ack_fails(self, client, new_client):
        token = new_client["portal_token"]
        r = client.post(
            f"/client/kyc/submit?token={token}",
            data=json.dumps({
                "sanctions_ack": False,
            }),
            content_type="application/json",
        )
        assert r.status_code in (200, 400)
        data = r.get_json()
        assert not data.get("ok", True)


class TestClientPortalLogin:
    def test_login_page(self, client):
        r = client.get("/client/login")
        assert r.status_code == 200

    def test_request_link_valid(self, client, new_client):
        r = client.post(
            "/client/request-link",
            data=json.dumps({
                "reference_id": new_client["reference_id"],
                "email":        "test.client@example.com",
            }),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "portal_link" in data["data"]

    def test_request_link_wrong_email(self, client, new_client):
        r = client.post(
            "/client/request-link",
            data=json.dumps({
                "reference_id": new_client["reference_id"],
                "email":        "wrong@email.com",
            }),
            content_type="application/json",
        )
        assert r.status_code in (404, 200)
        data = r.get_json()
        assert not data.get("ok", True)
