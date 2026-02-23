"""
test_auth.py — Authentication route tests.
"""

import json


class TestLogin:
    def test_login_page_loads(self, client):
        r = client.get("/auth/login")
        assert r.status_code == 200

    def test_login_wrong_password(self, client):
        r = client.post(
            "/auth/login",
            data=json.dumps({"email": "admin@test.ae", "password": "wrongpass"}),
            content_type="application/json",
        )
        assert r.status_code in (401, 400, 200)
        data = r.get_json()
        assert data is not None
        assert not data.get("ok", True)  # should fail

    def test_login_success(self, client):
        r = client.post(
            "/auth/login",
            data=json.dumps({"email": "admin@test.ae", "password": "testpass"}),
            content_type="application/json",
        )
        # The login route may redirect (302) or return JSON (200)
        assert r.status_code in (200, 302)

    def test_logout(self, admin_client):
        r = admin_client.post("/auth/logout")
        assert r.status_code in (200, 302)
