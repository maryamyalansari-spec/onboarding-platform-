"""
notifications.py — Background Celery tasks for email notifications.

Each task gracefully degrades if SendGrid is not configured.
"""

import logging
from tasks.celery_app import celery

log = logging.getLogger(__name__)


@celery.task(bind=True, name="tasks.send_email", max_retries=3, default_retry_delay=60)
def send_email(self, to: str, subject: str, html_body: str, text_body: str = ""):
    """
    Generic email send task. Retries up to 3 times on failure.
    """
    from utils.email import send_email as _send
    try:
        ok = _send(to_email=to, subject=subject, html_body=html_body, text_body=text_body)
        if not ok:
            raise RuntimeError(f"SendGrid returned failure for {to}")
    except Exception as exc:
        log.warning(f"Email task failed ({exc}), retrying…")
        raise self.retry(exc=exc)


@celery.task(bind=True, name="tasks.send_portal_link_email", max_retries=3, default_retry_delay=60)
def send_portal_link_email(self, client_id: str, portal_link: str):
    """
    Send the portal access link to the client.
    Called after request-link API and during intake completion.
    """
    try:
        from database import db
        from models import Client, LawFirm
        from utils.email import send_email as _send, portal_link_email
        from flask import current_app

        client = Client.query.get(client_id)
        if not client or not client.email:
            log.warning(f"send_portal_link_email: no client or email for {client_id}")
            return

        firm = LawFirm.query.get(client.firm_id)
        firm_name = firm.firm_name if firm else "Your Law Firm"

        base_url = current_app.config.get("PORTAL_BASE_URL", "").rstrip("/")
        full_url = base_url + portal_link

        subject, html = portal_link_email(
            client_name=client.full_name,
            reference_id=client.reference_id,
            portal_url=full_url,
            firm_name=firm_name,
        )
        ok = _send(to_email=client.email, subject=subject, html_body=html)
        if not ok:
            raise RuntimeError("SendGrid send returned False")

    except Exception as exc:
        log.warning(f"send_portal_link_email failed: {exc}")
        raise self.retry(exc=exc)


@celery.task(bind=True, name="tasks.send_conflict_clear_email", max_retries=3, default_retry_delay=60)
def send_conflict_clear_email(self, client_id: str):
    """
    Notify the client that their conflict check came back clear
    and they can proceed with their intake.
    """
    try:
        from database import db
        from models import Client, LawFirm
        from utils.email import send_email as _send, conflict_clear_email
        from flask import current_app

        client = Client.query.get(client_id)
        if not client or not client.email:
            return

        firm = LawFirm.query.get(client.firm_id)
        firm_name = firm.firm_name if firm else "Your Law Firm"

        base_url   = current_app.config.get("PORTAL_BASE_URL", "").rstrip("/")
        portal_link = f"{base_url}/client/{client.reference_id}?token={client.portal_token}"

        subject, html = conflict_clear_email(
            client_name=client.full_name,
            reference_id=client.reference_id,
            portal_url=portal_link,
            firm_name=firm_name,
        )
        ok = _send(to_email=client.email, subject=subject, html_body=html)
        if not ok:
            raise RuntimeError("SendGrid send returned False")

    except Exception as exc:
        log.warning(f"send_conflict_clear_email failed: {exc}")
        raise self.retry(exc=exc)


@celery.task(bind=True, name="tasks.send_status_email", max_retries=3, default_retry_delay=60)
def send_status_email(self, client_id: str, new_status: str):
    """
    Send an approval or rejection email to the client when the admin
    updates their status to 'approved' or 'rejected'.
    """
    try:
        from database import db
        from models import Client, LawFirm
        from utils.email import send_email as _send, approval_email, rejection_email
        from flask import current_app

        client = Client.query.get(client_id)
        if not client or not client.email:
            return

        firm = LawFirm.query.get(client.firm_id)
        firm_name = firm.firm_name if firm else "Your Law Firm"

        if new_status == "approved":
            subject, html = approval_email(client.full_name, client.reference_id, firm_name)
        elif new_status == "rejected":
            subject, html = rejection_email(client.full_name, client.reference_id, firm_name)
        else:
            return  # Only email for terminal statuses

        ok = _send(to_email=client.email, subject=subject, html_body=html)
        if not ok:
            raise RuntimeError("SendGrid send returned False")

    except Exception as exc:
        log.warning(f"send_status_email failed: {exc}")
        raise self.retry(exc=exc)
