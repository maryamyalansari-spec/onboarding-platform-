"""
utils/email.py — SendGrid email helpers.

All outbound email goes through `send_email()`. It is a no-op if
SENDGRID_API_KEY is not configured, so the app degrades gracefully
in development.
"""

import logging

log = logging.getLogger(__name__)


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str = "",
    from_email: str = "",
    from_name: str = "",
) -> bool:
    """
    Send a transactional email via SendGrid.

    Returns True on success, False on failure.
    Never raises — errors are logged and swallowed so callers can
    proceed without email being a hard dependency.
    """
    from flask import current_app

    api_key       = current_app.config.get("SENDGRID_API_KEY", "")
    default_from  = current_app.config.get("SENDGRID_FROM_EMAIL", "noreply@itifaq.ae")

    if not api_key:
        log.warning(f"SendGrid not configured — email to {to_email} suppressed.")
        return False

    from_addr = from_email or default_from
    plain     = text_body or _html_to_plain(html_body)

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content

        message = Mail(
            from_email=Email(from_addr, from_name or "Itifaq Onboarding"),
            to_emails=To(to_email),
            subject=subject,
        )
        message.add_content(Content("text/plain", plain))
        message.add_content(Content("text/html", html_body))

        sg = SendGridAPIClient(api_key)
        response = sg.send(message)

        if response.status_code in (200, 202):
            log.info(f"Email sent to {to_email}: {subject!r} (status {response.status_code})")
            return True
        else:
            log.error(f"SendGrid returned {response.status_code} sending to {to_email}")
            return False

    except Exception as exc:
        log.error(f"SendGrid error sending to {to_email}: {exc}")
        return False


def _html_to_plain(html: str) -> str:
    """Naïve HTML → plain text fallback (strips tags)."""
    import re
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Email templates ───────────────────────────────────────────────────────────

def portal_link_email(client_name: str, reference_id: str, portal_url: str, firm_name: str) -> tuple[str, str]:
    """Returns (subject, html_body) for a portal link email."""
    subject = f"Your Itifaq Portal Access Link — {reference_id}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#1e293b;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0f1a2e;margin-bottom:4px;">{firm_name}</h2>
  <hr style="border:none;border-top:2px solid #2962cc;margin-bottom:24px;">
  <p>Dear <strong>{client_name}</strong>,</p>
  <p>Your secure client portal is ready. Click the button below to access your portal
     and complete your intake process.</p>
  <p style="text-align:center;margin:32px 0;">
    <a href="{portal_url}" style="background:#2962cc;color:#fff;text-decoration:none;
       padding:12px 28px;border-radius:4px;font-weight:600;display:inline-block;">
      Access My Portal
    </a>
  </p>
  <p style="font-size:0.85em;color:#64748b;">
    Your reference number is <strong>{reference_id}</strong>.<br>
    If the button does not work, copy this link into your browser:<br>
    <a href="{portal_url}" style="color:#2962cc;word-break:break-all;">{portal_url}</a>
  </p>
  <p style="font-size:0.8em;color:#94a3b8;margin-top:32px;">
    This link expires in 30 days. Do not share it with others.
  </p>
</body>
</html>
"""
    return subject, html


def conflict_clear_email(client_name: str, reference_id: str, portal_url: str, firm_name: str) -> tuple[str, str]:
    """Returns (subject, html_body) for a conflict-clear notification."""
    subject = f"Your intake has been reviewed — {reference_id}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#1e293b;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0f1a2e;margin-bottom:4px;">{firm_name}</h2>
  <hr style="border:none;border-top:2px solid #2962cc;margin-bottom:24px;">
  <p>Dear <strong>{client_name}</strong>,</p>
  <p>We have completed our initial conflict-of-interest check for your matter
     (<strong>{reference_id}</strong>). No conflicts were identified and your file
     has been passed to our team for review.</p>
  <p>You can now provide a statement and upload any supporting documents via your portal:</p>
  <p style="text-align:center;margin:32px 0;">
    <a href="{portal_url}" style="background:#2962cc;color:#fff;text-decoration:none;
       padding:12px 28px;border-radius:4px;font-weight:600;display:inline-block;">
      Open My Portal
    </a>
  </p>
  <p style="font-size:0.85em;color:#64748b;">
    Reference: <strong>{reference_id}</strong>
  </p>
</body>
</html>
"""
    return subject, html


def approval_email(client_name: str, reference_id: str, firm_name: str) -> tuple[str, str]:
    """Returns (subject, html_body) for a case-approval notification."""
    subject = f"Your matter has been accepted — {reference_id}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#1e293b;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0f1a2e;margin-bottom:4px;">{firm_name}</h2>
  <hr style="border:none;border-top:2px solid #16a34a;margin-bottom:24px;">
  <p>Dear <strong>{client_name}</strong>,</p>
  <p>We are pleased to inform you that your matter (<strong>{reference_id}</strong>)
     has been accepted and your engagement letter is being prepared.</p>
  <p>Our team will be in touch shortly with next steps.</p>
  <p style="font-size:0.85em;color:#64748b;">
    If you have any questions, please reply to this email or contact our office.
  </p>
</body>
</html>
"""
    return subject, html


def rejection_email(client_name: str, reference_id: str, firm_name: str) -> tuple[str, str]:
    """Returns (subject, html_body) for a case-rejection notification."""
    subject = f"Update on your matter — {reference_id}"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;color:#1e293b;max-width:560px;margin:0 auto;padding:24px;">
  <h2 style="color:#0f1a2e;margin-bottom:4px;">{firm_name}</h2>
  <hr style="border:none;border-top:2px solid #dc2626;margin-bottom:24px;">
  <p>Dear <strong>{client_name}</strong>,</p>
  <p>Thank you for reaching out to <strong>{firm_name}</strong> regarding your matter
     (<strong>{reference_id}</strong>).</p>
  <p>After careful review, we regret that we are unable to accept your matter at this time.
     This may be due to a conflict of interest or other internal reasons.</p>
  <p>We recommend seeking assistance from another qualified legal firm.</p>
  <p style="font-size:0.85em;color:#64748b;">
    We appreciate your understanding.
  </p>
</body>
</html>
"""
    return subject, html
