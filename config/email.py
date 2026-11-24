"""
Email for ResumeSculpt: the sign-up verification link and the password-reset link.

Two ways to send, picked by what is configured:

1. Resend, an email API over HTTPS (production). Used when RESEND_API_KEY is set. Works on
   hosts that block outgoing SMTP (Render's free instances do), and sends from your own
   domain once it is verified in Resend.
       RESEND_API_KEY   re_... (Resend dashboard > API Keys, "Sending access")
       MAIL_FROM        an address on the verified domain, e.g. no-reply@resumesculpt.app
2. SMTP (local development, or any host that allows it). Gmail by default.
       MAIL_FROM        the SMTP login address, e.g. you@gmail.com
       MAIL_PASSWORD    its password; for Gmail an App Password (16 letters, no spaces)
       SMTP_HOST, SMTP_PORT   default smtp.gmail.com and 587 (STARTTLS)

Both use:
    MAIL_FROM_NAME   display name (default: Sculpt)
    DASHBOARD_URL    the website; the emails link to it and load its logo
    MAIL_REPLY_TO    optional: where replies go (useful when MAIL_FROM is a no-reply address)
    APP_BASE_URL     the backend's address; the links in the emails point at it
"""
from config import branding
import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger("Sculpt")

RESEND_URL = "https://api.resend.com/emails"


def _mask(email: str) -> str:
    """u***@example.com, so addresses don't end up in the logs."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _send_resend(api_key: str, sender: str, to_email: str, subject: str, html_body: str, reply_to: str,
                 text_body: str = "") -> bool:
    payload = {"from": sender, "to": [to_email], "subject": subject, "html": html_body}
    if text_body:
        payload["text"] = text_body
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        res = httpx.post(RESEND_URL, json=payload, timeout=15,
                         headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as e:
        logger.error(f"[EMAIL] Resend unreachable sending to {_mask(to_email)}: {e}")
        return False
    if res.status_code >= 300:
        # Resend explains the problem in "message" (e.g. the domain isn't verified yet)
        try:
            reason = res.json().get("message") or res.text
        except ValueError:
            reason = res.text
        logger.error(f"[EMAIL] Resend refused '{subject}' to {_mask(to_email)}: {res.status_code} {reason[:300]}")
        return False
    logger.info(f"[EMAIL] Sent '{subject}' to {_mask(to_email)} via Resend")
    return True


def _send_smtp(mail_from: str, from_name: str, to_email: str, subject: str, html_body: str, reply_to: str,
               text_body: str = "") -> bool:
    mail_password = os.getenv("MAIL_PASSWORD", "")
    if not mail_password:
        logger.warning("[EMAIL] Neither RESEND_API_KEY nor MAIL_PASSWORD is set: email not sent.")
        return False
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{mail_from}>"
    msg["To"]      = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    if text_body:  # the plain part goes first; clients show the last part they can render
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(mail_from, mail_password)
            server.sendmail(mail_from, [to_email], msg.as_string())
        logger.info(f"[EMAIL] Sent '{subject}' to {_mask(to_email)} via SMTP")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send to {_mask(to_email)} via SMTP: {e}")
        return False


def _send(to_email: str, subject: str, html_body: str, text_body: str = "") -> bool:
    """Send one email. Returns True on success; never raises."""
    mail_from = os.getenv("MAIL_FROM", "").strip()
    from_name = os.getenv("MAIL_FROM_NAME", "Sculpt").strip() or "Sculpt"
    reply_to  = os.getenv("MAIL_REPLY_TO", "").strip()
    if not mail_from:
        logger.warning("[EMAIL] MAIL_FROM not set: email not sent.")
        return False

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if api_key:
        return _send_resend(api_key, f"{from_name} <{mail_from}>", to_email, subject, html_body, reply_to, text_body)
    return _send_smtp(mail_from, from_name, to_email, subject, html_body, reply_to, text_body)


def _first_name(full_name) -> str:
    name = str(full_name or "").strip().split(" ")[0]
    return name[:40] if name else "there"


def send_verification_email(to_email: str, full_name: str | None, token: str) -> bool:
    """Send the email-verification link to a newly signed-up user (valid 24 hours)."""
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    content = dict(
        heading=f"Hi {_first_name(full_name)}, confirm your email",
        paragraphs=[
            "Thanks for joining Sculpt. Confirm this address to start tailoring your resume: "
            "uploading it, scoring it against a job and generating a version for the role.",
        ],
        button_label="Verify email address",
        button_url=f"{base_url}/auth/verify-email?token={token}",
        note="This link expires in 24 hours. If you didn't create a Sculpt account, you can ignore this email.",
        footer_reason="You're receiving this because this address was used to sign up for Sculpt.",
    )
    return _send(
        to_email, "Verify your email for Sculpt",
        branding.email_html(preheader="Confirm your email to start tailoring your resume.",
                            kicker="Verify your email", **content),
        branding.email_text(**content),
    )


def send_password_reset_email(to_email: str, full_name: str | None, token: str) -> bool:
    """Send a password-reset link (valid 30 minutes)."""
    base_url = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    content = dict(
        heading=f"Hi {_first_name(full_name)}, reset your password",
        paragraphs=[
            "We received a request to reset the password for your Sculpt account. "
            "Choose a new one with the button below.",
        ],
        button_label="Choose a new password",
        button_url=f"{base_url}/auth/reset-password?token={token}",
        note="This link expires in 30 minutes and works once. If you didn't ask for this, ignore this email: "
             "your password stays the same.",
        footer_reason="You're receiving this because a password reset was requested for this address on Sculpt.",
    )
    return _send(
        to_email, "Reset your Sculpt password",
        branding.email_html(preheader="Your link to choose a new Sculpt password.",
                            kicker="Password reset", **content),
        branding.email_text(**content),
    )
