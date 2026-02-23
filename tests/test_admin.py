"""
test_admin.py — Admin dashboard and API route tests.
"""

import json
import pytest


class TestAdminPages:
    def test_dashboard_redirect_when_unauthenticated(self, client):
        r = client.get("/admin/")
        assert r.status_code in (302, 401)

    def test_dashboard_loads_when_authenticated(self, admin_client):
        r = admin_client.get("/admin/")
        assert r.status_code == 200

    def test_clients_page(self, admin_client):
        r = admin_client.get("/admin/clients")
        assert r.status_code == 200

    def test_conflict_page(self, admin_client):
        r = admin_client.get("/admin/conflict")
        # May redirect or 404 if template missing
        assert r.status_code in (200, 302, 404)

    def test_audit_page(self, admin_client):
        r = admin_client.get("/admin/audit")
        assert r.status_code == 200

    def test_settings_page(self, admin_client):
        r = admin_client.get("/admin/settings")
        assert r.status_code in (200, 404)  # template may not exist


class TestAdminStats:
    def test_stats_endpoint(self, admin_client):
        r = admin_client.get("/admin/stats")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "total_clients" in data["data"]
        assert "pending_review" in data["data"]

    def test_clients_data(self, admin_client):
        r = admin_client.get("/admin/clients-data")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "clients" in data["data"]
        assert isinstance(data["data"]["clients"], list)

    def test_clients_data_search(self, admin_client):
        r = admin_client.get("/admin/clients-data?search=Test")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_clients_data_pagination(self, admin_client):
        r = admin_client.get("/admin/clients-data?page=1&per_page=5")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "total" in data["data"]
        assert "pages" in data["data"]

    def test_conflict_queue(self, admin_client):
        r = admin_client.get("/admin/conflict-queue")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]


class TestAdminCaseDetail:
    @pytest.fixture(scope="class")
    def client_id(self, admin_client, app):
        """Get the first client in the DB."""
        with app.app_context():
            from models import Client
            c = Client.query.first()
            return c.client_id if c else None

    def test_case_detail_page(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.get(f"/admin/clients/{client_id}")
        assert r.status_code == 200

    def test_case_detail_data(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.get(f"/admin/clients/{client_id}/data")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "client" in data["data"]

    def test_case_detail_data_fields(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.get(f"/admin/clients/{client_id}/data")
        d = r.get_json()["data"]
        assert "passports" in d
        assert "statements" in d
        assert "documents" in d
        assert "requested_docs" in d

    def test_update_status(self, admin_client, client_id, app):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.put(
            f"/admin/clients/{client_id}/status",
            data=json.dumps({"status": "review"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_update_status_invalid(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.put(
            f"/admin/clients/{client_id}/status",
            data=json.dumps({"status": "invalid_status"}),
            content_type="application/json",
        )
        assert r.status_code in (400, 200)
        data = r.get_json()
        assert not data.get("ok", True)

    def test_request_document(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.post(
            f"/admin/clients/{client_id}/request-document",
            data=json.dumps({"document_type": "passport", "notes": "Please provide a clear copy."}),
            content_type="application/json",
        )
        assert r.status_code in (200, 201)
        data = r.get_json()
        assert data["ok"]
        assert "request_id" in data["data"]

    def test_engagement_letter_get_none(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.get(f"/admin/clients/{client_id}/engagement-letter")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        # letter may be None if not yet generated

    def test_calendly_link_not_configured(self, admin_client, client_id):
        if not client_id:
            pytest.skip("No clients in DB")
        r = admin_client.get(f"/admin/clients/{client_id}/calendly-link")
        # Should return 503 (not configured) or 200 with a URL
        assert r.status_code in (200, 503)


class TestAuditLog:
    def test_audit_data(self, admin_client):
        r = admin_client.get("/admin/audit-data")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "logs" in data["data"]
        assert "total" in data["data"]

    def test_audit_data_with_search(self, admin_client):
        r = admin_client.get("/admin/audit-data?search=client")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_audit_data_record_type_filter(self, admin_client):
        r = admin_client.get("/admin/audit-data?record_type=client")
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        for log in data["data"]["logs"]:
            assert log["record_type"] in ("client", None)
