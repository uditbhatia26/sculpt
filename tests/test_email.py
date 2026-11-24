"""
test_email.py
=============
Sending verification and reset emails: Resend over HTTPS when RESEND_API_KEY is set (works
where SMTP is blocked), SMTP otherwise; failures return False instead of raising; names are
escaped in the HTML; and the resend endpoint reports a failed send.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import httpx
from fastapi.testclient import TestClient

import main
from config import email as mail
from main import app, get_current_user, get_db

RESEND_ENV = {"RESEND_API_KEY": "re_test", "MAIL_FROM": "no-reply@resumesculpt.app",
              "MAIL_FROM_NAME": "Sculpt", "MAIL_REPLY_TO": "help@example.com",
              "APP_BASE_URL": "https://api.resumesculpt.app"}
SMTP_ENV = {"RESEND_API_KEY": "", "MAIL_FROM": "me@gmail.com", "MAIL_PASSWORD": "abcdabcdabcdabcd"}


def response(status, body=None):
    r = MagicMock(status_code=status, text=str(body))
    r.json.return_value = body or {}
    return r


class TestResend(unittest.TestCase):
    def test_sends_over_https_from_the_domain_with_the_link(self):
        with patch.dict(os.environ, RESEND_ENV), \
             patch.object(mail.httpx, "post", return_value=response(200, {"id": "e1"})) as post, \
             patch.object(mail.smtplib, "SMTP") as smtp:
            self.assertTrue(mail.send_verification_email("user@example.com", "Jane", "tok123"))
        smtp.assert_not_called()
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertEqual(url, "https://api.resend.com/emails")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer re_test")
        self.assertEqual(body["from"], "Sculpt <no-reply@resumesculpt.app>")
        self.assertEqual(body["to"], ["user@example.com"])
        self.assertEqual(body["reply_to"], "help@example.com")
        self.assertIn("https://api.resumesculpt.app/auth/verify-email?token=tok123", body["html"])

    def test_refusal_returns_false(self):
        with patch.dict(os.environ, RESEND_ENV), \
             patch.object(mail.httpx, "post", return_value=response(403, {"message": "domain is not verified"})):
            self.assertFalse(mail.send_password_reset_email("user@example.com", None, "tok"))

    def test_network_error_returns_false(self):
        with patch.dict(os.environ, RESEND_ENV), \
             patch.object(mail.httpx, "post", side_effect=httpx.ConnectError("down")):
            self.assertFalse(mail.send_verification_email("user@example.com", None, "tok"))

    def test_name_is_escaped(self):
        with patch.dict(os.environ, RESEND_ENV), \
             patch.object(mail.httpx, "post", return_value=response(200, {"id": "e1"})) as post:
            mail.send_verification_email("user@example.com", '<a href="https://evil.test">click</a>', "tok")
        html_body = post.call_args.kwargs["json"]["html"]
        self.assertNotIn('<a href="https://evil.test">', html_body)
        self.assertIn("&lt;a href=", html_body)


class TestSmtpFallback(unittest.TestCase):
    def test_without_a_resend_key_smtp_is_used(self):
        server = MagicMock()
        smtp = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=server), __exit__=MagicMock(return_value=False)))
        with patch.dict(os.environ, SMTP_ENV), patch.object(mail.smtplib, "SMTP", smtp), \
             patch.object(mail.httpx, "post") as post:
            self.assertTrue(mail.send_verification_email("user@example.com", "Jane", "tok"))
        post.assert_not_called()
        self.assertEqual(smtp.call_args.args[:2], ("smtp.gmail.com", 587))
        server.login.assert_called_once_with("me@gmail.com", "abcdabcdabcdabcd")

    def test_blocked_network_returns_false(self):
        """What Render's free plan does to SMTP."""
        with patch.dict(os.environ, SMTP_ENV), \
             patch.object(mail.smtplib, "SMTP", side_effect=OSError(101, "Network is unreachable")):
            self.assertFalse(mail.send_verification_email("user@example.com", None, "tok"))

    def test_nothing_configured_returns_false(self):
        with patch.dict(os.environ, {"RESEND_API_KEY": "", "MAIL_FROM": "", "MAIL_PASSWORD": ""}):
            self.assertFalse(mail.send_verification_email("user@example.com", None, "tok"))


class TestResendEndpoint(unittest.TestCase):
    def setUp(self):
        self.user = MagicMock(email="user@example.com", full_name="Jane", email_verified=False,
                              email_verification_sent_at=None)
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: MagicMock()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_failed_send_is_reported(self):
        with patch.object(main, "send_verification_email", return_value=False):
            res = self.client.post("/auth/resend-verification")
        self.assertEqual(res.status_code, 502)

    def test_successful_send(self):
        with patch.object(main, "send_verification_email", return_value=True):
            res = self.client.post("/auth/resend-verification")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
