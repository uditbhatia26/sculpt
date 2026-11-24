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
    MAIL_REPLY_TO    optional: where replies go (useful when MAIL_FROM is a no-reply address)
    APP_BASE_URL     the backend's address; the links in the emails point at it
"""
import html as html_lib  # not "html": the email builders use that name for the body
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


def _send_resend(api_key: str, sender: str, to_email: str, subject: str, html_body: str, reply_to: str) -> bool:
    payload = {"from": sender, "to": [to_email], "subject": subject, "html": html_body}
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


def _send_smtp(mail_from: str, from_name: str, to_email: str, subject: str, html_body: str, reply_to: str) -> bool:
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


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Send one email. Returns True on success; never raises."""
    mail_from = os.getenv("MAIL_FROM", "").strip()
    from_name = os.getenv("MAIL_FROM_NAME", "Sculpt").strip() or "Sculpt"
    reply_to  = os.getenv("MAIL_REPLY_TO", "").strip()
    if not mail_from:
        logger.warning("[EMAIL] MAIL_FROM not set: email not sent.")
        return False

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if api_key:
        return _send_resend(api_key, f"{from_name} <{mail_from}>", to_email, subject, html_body, reply_to)
    return _send_smtp(mail_from, from_name, to_email, subject, html_body, reply_to)


def send_verification_email(to_email: str, full_name: str | None, token: str) -> bool:
    """Send the email-verification link to a newly signed-up user."""
    base_url    = os.getenv("APP_BASE_URL", "http://localhost:8000")
    verify_url  = f"{base_url}/auth/verify-email?token={token}"
    name_label  = html_lib.escape(full_name or "there")  # a name is user input: never raw HTML

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background:#0f1117; margin:0; padding:40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td align="center">
          <table width="560" cellpadding="0" cellspacing="0"
                 style="background:#1a1d2e; border-radius:12px; overflow:hidden;">
            <!-- Header -->
            <tr>
              <td style="background:linear-gradient(135deg,#6c63ff,#a78bfa);
                         padding:32px 40px; text-align:center;">
                <h1 style="color:#fff; margin:0; font-size:24px; letter-spacing:-0.5px;">
                  ✦ ResumeSculpt
                </h1>
                <p style="color:rgba(255,255,255,0.8); margin:8px 0 0; font-size:14px;">
                  AI-Powered Resume Optimization
                </p>
              </td>
            </tr>
            <!-- Body -->
            <tr>
              <td style="padding:40px;">
                <h2 style="color:#e2e8f0; margin:0 0 16px; font-size:20px;">
                  Verify your email, {name_label} 👋
                </h2>
                <p style="color:#94a3b8; line-height:1.6; margin:0 0 24px;">
                  Thanks for signing up. Click the button below to verify your email
                  address and unlock full access to ResumeSculpt.
                </p>
                <table cellpadding="0" cellspacing="0">
                  <tr>
                    <td align="center"
                        style="background:linear-gradient(135deg,#6c63ff,#a78bfa);
                               border-radius:8px;">
                      <a href="{verify_url}"
                         style="display:inline-block; padding:14px 32px;
                                color:#fff; font-weight:600; font-size:15px;
                                text-decoration:none; border-radius:8px;">
                        Verify Email Address
                      </a>
                    </td>
                  </tr>
                </table>
                <p style="color:#64748b; font-size:13px; margin:24px 0 0;">
                  This link expires in <strong style="color:#94a3b8;">24 hours</strong>.
                  If you didn't create an account, you can safely ignore this email.
                </p>
                <hr style="border:none; border-top:1px solid #2d3148; margin:32px 0;">
                <p style="color:#475569; font-size:12px; margin:0;">
                  Or copy this link into your browser:<br>
                  <a href="{verify_url}"
                     style="color:#6c63ff; word-break:break-all;">{verify_url}</a>
                </p>
              </td>
            </tr>
            <!-- Footer -->
            <tr>
              <td style="padding:20px 40px; background:#13162a; text-align:center;">
                <p style="color:#475569; font-size:12px; margin:0;">
                  © 2026 ResumeSculpt. All rights reserved.
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """
    return _send(to_email, "Verify your ResumeSculpt email", html)


def send_password_reset_email(to_email: str, full_name: str | None, token: str) -> bool:
    """Send a password-reset link (valid 30 minutes)."""
    base_url   = os.getenv("APP_BASE_URL", "http://localhost:8000")
    reset_url  = f"{base_url}/auth/reset-password?token={token}"
    name_label = html_lib.escape(full_name or "there")  # a name is user input: never raw HTML
    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"></head>
    <body style="font-family:'Segoe UI',Arial,sans-serif;background:#0f1117;margin:0;padding:40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#1a1d2e;border-radius:12px;">
          <tr><td style="padding:40px;">
            <h2 style="color:#e2e8f0;margin:0 0 16px;">Reset your password, {name_label}</h2>
            <p style="color:#94a3b8;line-height:1.6;margin:0 0 24px;">
              We received a request to reset your ResumeSculpt password. This link expires in
              <strong>30 minutes</strong>. If you didn't ask for this, ignore this email.
            </p>
            <a href="{reset_url}" style="display:inline-block;padding:14px 32px;color:#fff;font-weight:600;
               text-decoration:none;border-radius:8px;background:linear-gradient(135deg,#6c63ff,#a78bfa);">
              Choose a new password</a>
            <p style="color:#475569;font-size:12px;margin:24px 0 0;word-break:break-all;">{reset_url}</p>
          </td></tr>
        </table>
      </td></tr></table>
    </body></html>
    """
    return _send(to_email, "Reset your ResumeSculpt password", html)
