"""
test_billing.py
===============
Razorpay billing with the API, the database and the clock mocked out: checkout signature,
webhook signature, plan changes on subscription events, cancelling, and the guard rails.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient

import billing_routes as billing
from main import app, get_current_user, get_db
from config.rate_limit import reset_rate_limits

ENV = {
    "RAZORPAY_KEY_ID": "rzp_test_key",
    "RAZORPAY_KEY_SECRET": "test_secret",
    "RAZORPAY_PLAN_ID": "plan_pro",
    "RAZORPAY_WEBHOOK_SECRET": "hook_secret",
}
PLAN = {"id": "plan_pro", "period": "monthly", "interval": 1, "item": {"name": "Sculpt Pro", "amount": 19900, "currency": "INR"}}
END = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())


def make_user(**over):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "user@example.com"
    u.full_name = "Jane"
    u.plan = "free"
    u.email_verified = True
    u.razorpay_subscription_id = None
    u.subscription_status = None
    u.pro_until = None
    for k, v in over.items():
        setattr(u, k, v)
    return u


def sign(secret, message: bytes):
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def fake_rzp(routes):
    """An rzp() stand-in answering from {(method, path): response or Exception}."""
    calls = []

    async def call(method, path, payload=None):
        calls.append((method, path, payload))
        res = routes.get((method, path))
        if isinstance(res, Exception):
            raise res
        if res is None:
            raise billing.RazorpayError(404, "not found")
        return res
    call.calls = calls
    return call


class Base(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()
        billing._plan_cache.update(at=0.0, id=None, data=None)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.user = make_user()
        self.db = MagicMock()
        self.db.query.return_value.filter.return_value.first.return_value = None
        self.db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.env.stop()


class TestPlan(Base):
    def test_price_comes_from_the_razorpay_plan(self):
        with patch.object(billing, "rzp", fake_rzp({("GET", "/plans/plan_pro"): PLAN})):
            body = self.client.get("/billing/plan").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["amount"], 19900)
        self.assertEqual(body["period"], "monthly")
        self.assertNotIn("test_secret", json.dumps(body))

    def test_disabled_without_keys(self):
        with patch.dict(os.environ, {"RAZORPAY_PLAN_ID": ""}):
            self.assertEqual(self.client.get("/billing/plan").json(), {"enabled": False})
            self.assertEqual(self.client.post("/billing/subscribe").status_code, 503)


class TestSubscribe(Base):
    def test_creates_a_subscription_and_returns_checkout_options(self):
        rzp = fake_rzp({("GET", "/plans/plan_pro"): PLAN, ("POST", "/subscriptions"): {"id": "sub_1", "status": "created"}})
        with patch.object(billing, "rzp", rzp):
            res = self.client.post("/billing/subscribe")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["subscription_id"], "sub_1")
        self.assertEqual(body["key_id"], "rzp_test_key")
        self.assertEqual(self.user.razorpay_subscription_id, "sub_1")
        self.assertEqual(self.user.subscription_status, "created")
        created = [c for c in rzp.calls if c[0] == "POST"][0][2]
        self.assertEqual(created["plan_id"], "plan_pro")
        self.assertEqual(created["notes"]["user_id"], str(self.user.id))

    def test_reuses_an_unpaid_checkout(self):
        self.user.razorpay_subscription_id = "sub_old"
        self.user.subscription_status = "created"
        rzp = fake_rzp({("GET", "/plans/plan_pro"): PLAN,
                        ("GET", "/subscriptions/sub_old"): {"id": "sub_old", "status": "created", "plan_id": "plan_pro"}})
        with patch.object(billing, "rzp", rzp):
            res = self.client.post("/billing/subscribe")
        self.assertEqual(res.json()["subscription_id"], "sub_old")
        self.assertFalse([c for c in rzp.calls if c[0] == "POST"])

    def test_already_pro_is_409(self):
        self.user.plan = "pro"
        self.user.subscription_status = "active"
        self.assertEqual(self.client.post("/billing/subscribe").status_code, 409)

    def test_unverified_email_is_403(self):
        self.user.email_verified = False
        self.assertEqual(self.client.post("/billing/subscribe").status_code, 403)

    def test_provider_down_is_502(self):
        with patch.object(billing, "rzp", fake_rzp({("GET", "/plans/plan_pro"): billing.RazorpayError(0, "down")})):
            self.assertEqual(self.client.post("/billing/subscribe").status_code, 502)


class TestVerify(Base):
    def _body(self, sub="sub_1", pay="pay_1", signature=None):
        sig = signature or sign("test_secret", f"{pay}|{sub}".encode())
        return {"razorpay_payment_id": pay, "razorpay_subscription_id": sub, "razorpay_signature": sig}

    def setUp(self):
        super().setUp()
        self.user.razorpay_subscription_id = "sub_1"
        self.user.subscription_status = "created"

    def test_valid_signature_switches_to_pro(self):
        rzp = fake_rzp({("GET", "/plans/plan_pro"): PLAN,
                        ("GET", "/subscriptions/sub_1"): {"id": "sub_1", "status": "active", "current_end": END},
                        ("GET", "/payments/pay_1"): {"id": "pay_1", "amount": 19900, "currency": "INR", "status": "captured"}})
        with patch.object(billing, "rzp", rzp):
            res = self.client.post("/billing/verify", json=self._body())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.user.plan, "pro")
        self.assertEqual(self.user.subscription_status, "active")
        self.assertEqual(int(self.user.pro_until.timestamp()), END)
        self.db.add.assert_called_once()   # the payment is recorded

    def test_still_activates_when_razorpay_is_slow_to_report(self):
        with patch.object(billing, "rzp", fake_rzp({("GET", "/plans/plan_pro"): PLAN})):
            res = self.client.post("/billing/verify", json=self._body())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.user.plan, "pro")
        self.assertGreater(self.user.pro_until, datetime.now(timezone.utc) + timedelta(days=29))

    def test_forged_signature_is_refused(self):
        with patch.object(billing, "rzp", fake_rzp({})):
            res = self.client.post("/billing/verify", json=self._body(signature="0" * 64))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.user.plan, "free")

    def test_someone_elses_subscription_is_refused(self):
        body = self._body(sub="sub_other")
        res = self.client.post("/billing/verify", json=body)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.user.plan, "free")


class TestCancel(Base):
    def test_cancel_keeps_pro_until_period_end(self):
        self.user.plan = "pro"
        self.user.razorpay_subscription_id = "sub_1"
        self.user.subscription_status = "active"
        rzp = fake_rzp({("POST", "/subscriptions/sub_1/cancel"): {"id": "sub_1", "status": "active", "current_end": END}})
        with patch.object(billing, "rzp", rzp):
            res = self.client.post("/billing/cancel")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.user.plan, "pro")
        self.assertEqual(self.user.subscription_status, "cancelling")
        self.assertEqual(rzp.calls[0][2], {"cancel_at_cycle_end": 1})

    def test_nothing_to_cancel_is_400(self):
        self.assertEqual(self.client.post("/billing/cancel").status_code, 400)


class TestWebhook(Base):
    def _post(self, event, secret="hook_secret"):
        raw = json.dumps(event).encode()
        return self.client.post("/billing/webhook", content=raw,
                                headers={"X-Razorpay-Signature": sign(secret, raw), "Content-Type": "application/json"})

    def _event(self, name, status, sub_id="sub_1", payment=None):
        payload = {"subscription": {"entity": {"id": sub_id, "status": status, "current_end": END}}}
        if payment:
            payload["payment"] = {"entity": payment}
        return {"entity": "event", "event": name, "payload": payload}

    def _with_user(self, user):
        self.db.query.return_value.filter.return_value.first.side_effect = [user, None]

    def test_unsigned_is_refused(self):
        self.assertEqual(self._post(self._event("subscription.charged", "active"), secret="wrong").status_code, 400)

    def test_charged_extends_pro_and_records_payment(self):
        user = make_user(razorpay_subscription_id="sub_1")
        self._with_user(user)
        res = self._post(self._event("subscription.charged", "active",
                                     payment={"id": "pay_2", "amount": 19900, "currency": "INR", "status": "captured"}))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(user.plan, "pro")
        self.assertEqual(int(user.pro_until.timestamp()), END)
        self.db.add.assert_called_once()

    def test_cancelled_returns_to_free(self):
        user = make_user(plan="pro", razorpay_subscription_id="sub_1", subscription_status="cancelling")
        self._with_user(user)
        self._post(self._event("subscription.cancelled", "cancelled"))
        self.assertEqual(user.plan, "free")
        self.assertEqual(user.subscription_status, "cancelled")

    def test_halted_after_failed_renewals_returns_to_free(self):
        user = make_user(plan="pro", razorpay_subscription_id="sub_1", subscription_status="pending")
        self._with_user(user)
        self._post(self._event("subscription.halted", "halted"))
        self.assertEqual(user.plan, "free")

    def test_scheduled_cancellation_survives_an_active_event(self):
        user = make_user(plan="pro", razorpay_subscription_id="sub_1", subscription_status="cancelling")
        self._with_user(user)
        self._post(self._event("subscription.updated", "active"))
        self.assertEqual(user.subscription_status, "cancelling")

    def test_unknown_subscription_is_ignored(self):
        res = self._post(self._event("subscription.cancelled", "cancelled", sub_id="sub_gone"))
        self.assertEqual(res.status_code, 200)


class TestLapse(unittest.TestCase):
    def test_cancelled_pro_lapses_after_period_end(self):
        user = make_user(plan="pro", subscription_status="cancelling",
                         pro_until=datetime.now(timezone.utc) - timedelta(hours=1))
        billing.expire_if_lapsed(user, MagicMock())
        self.assertEqual(user.plan, "free")

    def test_active_pro_is_left_alone(self):
        user = make_user(plan="pro", subscription_status="active",
                         pro_until=datetime.now(timezone.utc) - timedelta(hours=1))
        billing.expire_if_lapsed(user, MagicMock())
        self.assertEqual(user.plan, "pro")


if __name__ == "__main__":
    unittest.main()
