"""
Billing: Sculpt Pro as a Razorpay subscription.

Flow:  /billing/subscribe creates a subscription on the Pro plan and returns what Razorpay
       Checkout needs. The browser opens Checkout; on success it posts the signed result to
       /billing/verify, which checks the signature and switches the account to Pro.
       Razorpay's webhook (/billing/webhook) is the source of truth after that: renewals move
       pro_until forward, and a cancelled, halted or completed subscription returns the
       account to Free. Cancelling (/billing/cancel) stops renewal at the end of the paid period.

Configuration (all four are needed, or billing reports itself disabled):
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET   API keys (test keys start rzp_test_)
    RAZORPAY_PLAN_ID                       the Pro plan, created in the Razorpay dashboard
    RAZORPAY_WEBHOOK_SECRET                the secret set on the webhook in the dashboard

The price shown on the site is read from the Razorpay plan, so it is set in one place.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from config.auth import get_current_user
from config.database import get_db
from config.rate_limit import rate_limit
from models.database_models import Payment, User

logger = logging.getLogger("Sculpt")
router = APIRouter()

RAZORPAY_API = "https://api.razorpay.com/v1"
ACTIVE_STATES = {"active", "authenticated"}
ENDED_STATES = {"cancelled", "completed", "halted", "expired", "paused"}
# How many billing cycles a subscription may run before it needs renewing, per period
TOTAL_COUNT = {"daily": 365, "weekly": 104, "monthly": 60, "yearly": 10}
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 31, "yearly": 366}
PLAN_CACHE_SECONDS = 600


# ==================== CONFIG ====================

def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def billing_enabled() -> bool:
    return all(_env(n) for n in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_PLAN_ID"))


def _require_billing() -> None:
    if not billing_enabled():
        raise HTTPException(status_code=503, detail="Payments aren't open yet. Please check back soon.")


# ==================== RAZORPAY API ====================

class RazorpayError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


async def rzp(method: str, path: str, payload: Optional[dict] = None) -> dict:
    """Call the Razorpay API with the server's keys. Raises RazorpayError on an error status."""
    auth = (_env("RAZORPAY_KEY_ID"), _env("RAZORPAY_KEY_SECRET"))
    try:
        async with httpx.AsyncClient(timeout=15, auth=auth) as client:
            res = await client.request(method, f"{RAZORPAY_API}{path}", json=payload)
    except httpx.HTTPError as e:
        logger.error(f"[BILLING] Razorpay {method} {path} unreachable: {e}")
        raise RazorpayError(0, "unreachable")
    if res.status_code >= 400:
        try:
            message = res.json().get("error", {}).get("description") or res.text
        except ValueError:
            message = res.text
        logger.error(f"[BILLING] Razorpay {method} {path} -> {res.status_code}: {message[:300]}")
        raise RazorpayError(res.status_code, message)
    return res.json()


def _provider_down() -> HTTPException:
    return HTTPException(status_code=502, detail="We couldn't reach the payment provider. Please try again in a minute.")


_plan_cache: dict = {"at": 0.0, "id": None, "data": None}


async def get_plan() -> dict:
    """The Pro plan as {amount, currency, period, interval, name}, cached for a few minutes."""
    plan_id = _env("RAZORPAY_PLAN_ID")
    if _plan_cache["data"] and _plan_cache["id"] == plan_id and time.monotonic() - _plan_cache["at"] < PLAN_CACHE_SECONDS:
        return _plan_cache["data"]
    raw = await rzp("GET", f"/plans/{plan_id}")
    item = raw.get("item") or {}
    data = {
        "amount":   int(item.get("amount") or 0),
        "currency": item.get("currency") or "INR",
        "period":   raw.get("period") or "monthly",
        "interval": int(raw.get("interval") or 1),
        "name":     item.get("name") or "Sculpt Pro",
    }
    _plan_cache.update(at=time.monotonic(), id=plan_id, data=data)
    return data


# ==================== SIGNATURES ====================

def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature_valid(payment_id: str, subscription_id: str, signature: str) -> bool:
    """Razorpay signs a subscription payment as HMAC-SHA256(payment_id|subscription_id, key_secret)."""
    expected = _hmac_hex(_env("RAZORPAY_KEY_SECRET"), f"{payment_id}|{subscription_id}".encode())
    return hmac.compare_digest(expected, signature or "")


def webhook_signature_valid(body: bytes, signature: str) -> bool:
    secret = _env("RAZORPAY_WEBHOOK_SECRET")
    return bool(secret) and hmac.compare_digest(_hmac_hex(secret, body), signature or "")


# ==================== ACCOUNT STATE ====================

def _from_unix(ts) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    except (TypeError, ValueError, OSError):
        return None


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def apply_subscription(user: User, sub: dict) -> None:
    """Bring the account in line with a Razorpay subscription. Doesn't commit."""
    status = sub.get("status")
    end = _from_unix(sub.get("current_end"))
    if end:
        user.pro_until = end
    if status in ACTIVE_STATES:
        user.plan = "pro"
        if user.subscription_status != "cancelling":   # a scheduled cancellation stays scheduled
            user.subscription_status = "active"
    elif status == "pending":                           # a renewal failed and Razorpay is retrying
        user.subscription_status = "pending"
    elif status in ENDED_STATES:
        user.plan = "free"
        user.subscription_status = status


def expire_if_lapsed(user: User, db: Session) -> None:
    """A cancelled Pro whose paid period is over becomes Free, even if the webhook was missed."""
    until = _aware(user.pro_until)
    if user.plan == "pro" and user.subscription_status == "cancelling" and until and until < datetime.now(timezone.utc):
        user.plan = "free"
        user.subscription_status = "cancelled"
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"[BILLING] Could not lapse Pro for {user.id}: {e}")


def record_payment(db: Session, user: User, payment_id: str, subscription_id: Optional[str],
                   amount: int, currency: str, status: str = "captured") -> None:
    """Save a payment once. A payment already saved (by the webhook or by verify) is left alone."""
    if not payment_id or db.query(Payment.id).filter(Payment.razorpay_payment_id == payment_id).first():
        return
    db.add(Payment(user_id=user.id, razorpay_payment_id=payment_id, razorpay_subscription_id=subscription_id,
                   amount=int(amount or 0), currency=currency or "INR", status=status or "captured"))
    try:
        db.commit()
    except IntegrityError:   # saved by the other path a moment ago
        db.rollback()


def billing_state(user: User, db: Session) -> dict:
    payments = (db.query(Payment).filter(Payment.user_id == user.id)
                .order_by(Payment.created_at.desc()).limit(12).all())
    until = _aware(user.pro_until)
    return {
        "enabled":    billing_enabled(),
        "plan":       user.plan or "free",
        "status":     user.subscription_status,
        "pro_until":  until.isoformat() if until else None,
        "renews":     user.plan == "pro" and user.subscription_status in ("active", "pending"),
        "payments": [
            {"id": p.razorpay_payment_id, "amount": p.amount, "currency": p.currency, "status": p.status,
             "created_at": _aware(p.created_at).isoformat() if p.created_at else None}
            for p in payments
        ],
    }


# ==================== ENDPOINTS ====================

@router.get("/billing/plan")
async def billing_plan():
    """The Pro price for the site to show. Public: it's the same for everyone."""
    if not billing_enabled():
        return {"enabled": False}
    try:
        plan = await get_plan()
    except RazorpayError:
        return {"enabled": False}
    return {"enabled": True, "key_id": _env("RAZORPAY_KEY_ID"), **plan}


@router.get("/billing/status")
def billing_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expire_if_lapsed(user, db)
    return billing_state(user, db)


@router.post("/billing/subscribe", dependencies=[Depends(rate_limit("billing_subscribe", 12, 3600))])
async def billing_subscribe(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start (or reuse) a Pro subscription and return what Razorpay Checkout needs."""
    _require_billing()
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Verify your email address before upgrading. Check your inbox for the link.")
    await run_in_threadpool(expire_if_lapsed, user, db)
    if user.plan == "pro":
        until = _aware(user.pro_until)
        if user.subscription_status == "cancelling" and until:
            raise HTTPException(status_code=409, detail=f"Your Pro plan runs until {until:%d %B %Y}. You can subscribe again once it ends.")
        raise HTTPException(status_code=409, detail="Your account is already on Pro.")

    try:
        plan = await get_plan()
        sub = None
        # Reuse a checkout that was opened but never paid, rather than leave a trail of them
        if user.razorpay_subscription_id and user.subscription_status == "created":
            try:
                existing = await rzp("GET", f"/subscriptions/{user.razorpay_subscription_id}")
                if existing.get("status") == "created" and existing.get("plan_id") == _env("RAZORPAY_PLAN_ID"):
                    sub = existing
            except RazorpayError:
                sub = None
        if sub is None:
            sub = await rzp("POST", "/subscriptions", {
                "plan_id":         _env("RAZORPAY_PLAN_ID"),
                "total_count":     TOTAL_COUNT.get(plan["period"], 60),
                "customer_notify": 1,
                "notes":           {"user_id": str(user.id)},
            })
    except RazorpayError:
        raise _provider_down()

    def save():
        user.razorpay_subscription_id = sub["id"]
        user.subscription_status = "created"
        db.commit()
    await run_in_threadpool(save)

    return {
        "key_id":          _env("RAZORPAY_KEY_ID"),
        "subscription_id": sub["id"],
        "name":            "Sculpt",
        "description":     plan["name"],
        "amount":          plan["amount"],
        "currency":        plan["currency"],
        "prefill":         {"name": user.full_name or "", "email": user.email},
    }


class VerifyBody(BaseModel):
    razorpay_payment_id:      str = Field(..., min_length=4, max_length=64)
    razorpay_subscription_id: str = Field(..., min_length=4, max_length=64)
    razorpay_signature:       str = Field(..., min_length=16, max_length=256)


@router.post("/billing/verify", dependencies=[Depends(rate_limit("billing_verify", 30, 3600))])
async def billing_verify(body: VerifyBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Confirm a Checkout payment: check Razorpay's signature, then switch the account to Pro."""
    _require_billing()
    if body.razorpay_subscription_id != user.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="This payment doesn't match your account's checkout.")
    if not checkout_signature_valid(body.razorpay_payment_id, body.razorpay_subscription_id, body.razorpay_signature):
        logger.warning(f"[BILLING] Bad checkout signature for user {user.id}")
        raise HTTPException(status_code=400, detail="We couldn't verify this payment. If you were charged, email us and we'll sort it out.")

    # The signature proves the payment succeeded. Razorpay's own record supplies the dates and
    # amount; if it can't be reached right now, the plan's period and price stand in.
    plan = None
    try:
        plan = await get_plan()
    except RazorpayError:
        pass
    try:
        sub = await rzp("GET", f"/subscriptions/{body.razorpay_subscription_id}")
    except RazorpayError:
        sub = {}
    if sub.get("status") in (None, "created", "authenticated"):
        sub = {**sub, "status": "active"}
    if not sub.get("current_end"):
        days = PERIOD_DAYS.get((plan or {}).get("period", "monthly"), 31) * int((plan or {}).get("interval", 1))
        sub["current_end"] = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
    try:
        pay = await rzp("GET", f"/payments/{body.razorpay_payment_id}")
    except RazorpayError:
        pay = {}

    def save():
        apply_subscription(user, sub)
        db.commit()
        record_payment(db, user, body.razorpay_payment_id, body.razorpay_subscription_id,
                       pay.get("amount") or (plan or {}).get("amount", 0),
                       pay.get("currency") or (plan or {}).get("currency", "INR"),
                       pay.get("status") or "captured")
        return billing_state(user, db)
    state = await run_in_threadpool(save)
    logger.info(f"[BILLING] {user.id} is on Pro until {state['pro_until']}")
    return state


@router.post("/billing/cancel", dependencies=[Depends(rate_limit("billing_cancel", 10, 3600))])
async def billing_cancel(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop renewal. Pro stays until the end of the period already paid for."""
    _require_billing()
    if not user.razorpay_subscription_id or user.subscription_status not in ("active", "pending"):
        raise HTTPException(status_code=400, detail="There's no active subscription to cancel.")
    try:
        sub = await rzp("POST", f"/subscriptions/{user.razorpay_subscription_id}/cancel", {"cancel_at_cycle_end": 1})
    except RazorpayError:
        raise _provider_down()

    def save():
        end = _from_unix(sub.get("current_end"))
        if end:
            user.pro_until = end
        if sub.get("status") in ENDED_STATES:   # ended at once (e.g. it was still retrying a failed renewal)
            apply_subscription(user, sub)
        else:
            user.subscription_status = "cancelling"
        db.commit()
        return billing_state(user, db)
    return await run_in_threadpool(save)


def _handle_event(event: dict, db: Session) -> None:
    name = event.get("event") or ""
    payload = event.get("payload") or {}
    sub = (payload.get("subscription") or {}).get("entity") or {}
    if not name.startswith("subscription.") or not sub.get("id"):
        return
    # Only the subscription the account holds now may change it: an old one ending is ignored
    user = db.query(User).filter(User.razorpay_subscription_id == sub["id"]).first()
    if user is None:
        logger.info(f"[BILLING] Webhook {name} for unknown subscription {sub['id']}")
        return
    apply_subscription(user, sub)
    db.commit()
    pay = (payload.get("payment") or {}).get("entity") or {}
    if name == "subscription.charged" and pay.get("id"):
        record_payment(db, user, pay["id"], sub["id"], pay.get("amount"), pay.get("currency"), pay.get("status"))
    logger.info(f"[BILLING] Webhook {name}: {user.id} plan={user.plan} status={user.subscription_status}")


@router.post("/billing/webhook")
async def billing_webhook(request: Request, db: Session = Depends(get_db)):
    """Razorpay's notifications. Signed with the webhook secret; anything unsigned is refused."""
    body = await request.body()
    if not webhook_signature_valid(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(status_code=400, detail="Invalid signature.")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid body.")
    await run_in_threadpool(_handle_event, event, db)
    return {"ok": True}
