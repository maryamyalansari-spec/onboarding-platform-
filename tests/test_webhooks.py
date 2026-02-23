"""
test_webhooks.py — Webhook endpoint tests (Calendly, DocuSeal).
"""

import json
import pytest


class TestCalendlyWebhook:
    def test_invitee_created(self, admin_client, app):
        payload = {
            "event": "invitee.created",
            "payload": {
                "invitee": {
                    "email": "test.client@example.com",
                    "name":  "Test Client",
                    "cancel_url":     "https://calendly.com/cancel/abc",
                    "reschedule_url": "https://calendly.com/reschedule/abc",
                },
                "scheduled_event": {
                    "name":       "Initial Consultation",
                    "uri":        "https://api.calendly.com/scheduled_events/evt-abc123",
                    "start_time": "2026-03-01T10:00:00Z",
                    "end_time":   "2026-03-01T11:00:00Z",
                },
            },
        }
        r = admin_client.post(
            "/admin/webhooks/calendly",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_invitee_canceled(self, admin_client, app):
        payload = {
            "event": "invitee.canceled",
            "payload": {
                "invitee": {
                    "email": "test.client@example.com",
                    "name":  "Test Client",
                },
                "scheduled_event": {
                    "name": "Initial Consultation",
                    "uri":  "https://api.calendly.com/scheduled_events/evt-abc123",
                    "start_time": "2026-03-01T10:00:00Z",
                    "end_time":   "2026-03-01T11:00:00Z",
                },
            },
        }
        r = admin_client.post(
            "/admin/webhooks/calendly",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_unknown_event_ignored(self, admin_client):
        payload = {"event": "some.other.event", "payload": {}}
        r = admin_client.post(
            "/admin/webhooks/calendly",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "ignored" in data.get("message", "").lower()


class TestDocuSealWebhook:
    def test_unknown_submission_logged(self, admin_client):
        payload = {
            "event_type": "form.completed",
            "data": {"id": "nonexistent-submission-id-xyz"},
        }
        r = admin_client.post(
            "/admin/webhooks/docuseal",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]

    def test_irrelevant_event_ignored(self, admin_client):
        payload = {"event_type": "form.viewed", "data": {"id": "123"}}
        r = admin_client.post(
            "/admin/webhooks/docuseal",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["ok"]
        assert "ignored" in data.get("message", "").lower()
