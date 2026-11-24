"""
test_client_ip.py
=================
Which address rate limiting uses for a visitor, and that a configured setup can't be fooled
by a faked X-Forwarded-For.
"""

import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from starlette.requests import Request
from fastapi.testclient import TestClient

from config.rate_limit import client_ip, reset_rate_limits
from main import app


def request(headers: dict, peer="10.0.0.9"):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234)})


NO_CONFIG = {"CLIENT_IP_HEADER": "", "TRUSTED_PROXY_HOPS": ""}


class TestClientIp(unittest.TestCase):
    def test_no_proxy_uses_the_connection(self):
        with patch.dict(os.environ, NO_CONFIG):
            self.assertEqual(client_ip(request({})), "10.0.0.9")

    def test_default_keeps_the_old_behaviour(self):
        with patch.dict(os.environ, NO_CONFIG):
            self.assertEqual(client_ip(request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})), "6.6.6.6")

    def test_one_trusted_hop_ignores_a_faked_prefix(self):
        """The visitor sends X-Forwarded-For: 6.6.6.6; the host's proxy appends the real address."""
        with patch.dict(os.environ, {**NO_CONFIG, "TRUSTED_PROXY_HOPS": "1"}):
            self.assertEqual(client_ip(request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})), "203.0.113.7")

    def test_two_hops(self):
        with patch.dict(os.environ, {**NO_CONFIG, "TRUSTED_PROXY_HOPS": "2"}):
            self.assertEqual(client_ip(request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7, 172.16.0.1"})), "203.0.113.7")

    def test_host_header_wins_when_configured(self):
        with patch.dict(os.environ, {**NO_CONFIG, "CLIENT_IP_HEADER": "True-Client-IP"}):
            self.assertEqual(client_ip(request({"True-Client-IP": "203.0.113.7", "X-Forwarded-For": "6.6.6.6"})), "203.0.113.7")
            # header missing on this request: fall back instead of lumping everyone together
            self.assertEqual(client_ip(request({"X-Forwarded-For": "6.6.6.6"})), "6.6.6.6")

    def test_bad_hop_setting_falls_back(self):
        with patch.dict(os.environ, {**NO_CONFIG, "TRUSTED_PROXY_HOPS": "lots"}):
            self.assertEqual(client_ip(request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})), "6.6.6.6")


class TestClientIpEndpoint(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()

    def test_reports_the_callers_own_headers(self):
        with patch.dict(os.environ, {**NO_CONFIG, "TRUSTED_PROXY_HOPS": "1"}):
            body = TestClient(app).get("/client-ip", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"}).json()
        self.assertEqual(body["rate_limited_as"], "203.0.113.7")
        self.assertEqual(body["x_forwarded_for"], "6.6.6.6, 203.0.113.7")
        self.assertEqual(body["settings"]["TRUSTED_PROXY_HOPS"], "1")


if __name__ == "__main__":
    unittest.main()
