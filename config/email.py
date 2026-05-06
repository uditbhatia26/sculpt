"""
Email utility for ResumeSculpt.

Uses Python's built-in smtplib with Gmail SMTP (TLS on port 587).
Required env vars:
    MAIL_FROM          — your Gmail address (e.g. hello@gmail.com)
    MAIL_PASSWORD      — Gmail App Password (16 chars, no spaces)
    MAIL_FROM_NAME     — display name (default: ResumeSculpt)
    APP_BASE_URL       — base URL of the backend (e.g. https://api.resumesculpt.com)
"""
import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("Sculpt")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _send(to_email: str, subject: str, html_body: str) -> bool:
    """Low-level: open SMTP connection and send a single email. Returns True on success."""
    mail_from     = os.getenv("MAIL_FROM", "")
    mail_password = os.getenv("MAIL_PASSWORD", "")
    from_name     = os.getenv("MAIL_FROM_NAME", "ResumeSculpt")

    if not mail_from or not mail_password:
        logger.warning("[EMAIL] MAIL_FROM or MAIL_PASSWORD not set — email not sent.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{mail_from}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(mail_from, mail_password)
            server.sendmail(mail_from, [to_email], msg.as_string())
        logger.info(f"[EMAIL] Sent '{subject}' → {to_email}")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send to {to_email}: {e}")
        return False


def send_verification_email(to_email: str, full_name: str | None, token: str) -> bool:
    """Send the email-verification link to a newly signed-up user."""
    base_url    = os.getenv("APP_BASE_URL", "http://localhost:8000")
    verify_url  = f"{base_url}/auth/verify-email?token={token}"
    name_label  = full_name or "there"

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
