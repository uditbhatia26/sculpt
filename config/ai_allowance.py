"""
Daily allowances for server-paid AI calls that no weekly quota covers.

Generations (/optimize-resume) and ATS analyses have weekly quotas in main.py, and Chisel has
its own allowance. Everything else that calls the server's AI key is counted here, per user
per UTC day, so no single account can run up the provider bill:

    upload     /upload-resume (resume text -> YAML)
    jd_parse   /parse-jd, only when the job isn't cached yet
    explain    /explain-changes, only for a new explanation (saved ones are free to reopen)
    refine     /profile/refine

Calls made with the user's own key (BYOK) are not counted. A call that fails gives its unit
back, so a failure never uses up the allowance.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from models.database_models import AIDailyUsage

logger = logging.getLogger("Sculpt")

AI_DAILY_LIMITS = {
    "upload":   {"free": 5,  "pro": 15},
    "jd_parse": {"free": 30, "pro": 100},
    "explain":  {"free": 10, "pro": 40},
    "refine":   {"free": 30, "pro": 100},
}

_LABELS = {
    "upload":   "resume uploads",
    "jd_parse": "new job descriptions",
    "explain":  "change explanations",
    "refine":   "text polishes",
}


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def daily_limit(user, bucket: str) -> int:
    limits = AI_DAILY_LIMITS[bucket]
    return limits.get(getattr(user, "plan", None) or "free", limits["free"])


def reserve_ai_call(user, bucket: str, db: Session) -> date:
    """Atomically take one call from today's allowance for this bucket; returns the day.
    One INSERT ... ON CONFLICT DO UPDATE ... WHERE count < limit, so concurrent requests on
    any number of workers can't overshoot. Raises 429 when the day's allowance is used up."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if bucket not in AI_DAILY_LIMITS:
        raise ValueError(f"Unknown AI allowance bucket: {bucket}")
    day = utc_today()
    limit = daily_limit(user, bucket)
    stmt = pg_insert(AIDailyUsage).values(
        id=uuid.uuid4(), user_id=user.id, day=day, bucket=bucket, count=1,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ai_daily_usage_user_day_bucket",
        set_={"count": AIDailyUsage.count + 1, "updated_at": func.now()},
        where=AIDailyUsage.count < limit,
    ).returning(AIDailyUsage.count)
    try:
        new_count = db.execute(stmt).scalar()
        db.commit()
    except Exception:
        db.rollback()
        raise
    if new_count is None:
        label = _LABELS[bucket]
        pro = AI_DAILY_LIMITS[bucket]["pro"]
        upgrade = f" Pro allows {pro} a day." if (getattr(user, "plan", None) or "free") == "free" and pro > limit else ""
        raise HTTPException(
            status_code=429,
            detail=f"You've reached today's limit of {limit} {label}. It resets at midnight UTC.{upgrade}",
        )
    return day


def release_ai_call(user_id, bucket: str, day: date, db: Session) -> None:
    """Give back a call taken by reserve_ai_call() when the AI call failed. Best-effort."""
    try:
        db.rollback()
        db.query(AIDailyUsage).filter(
            AIDailyUsage.user_id == user_id,
            AIDailyUsage.day == day,
            AIDailyUsage.bucket == bucket,
            AIDailyUsage.count > 0,
        ).update({AIDailyUsage.count: AIDailyUsage.count - 1}, synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[AI ALLOWANCE] Could not give back a {bucket} call for {user_id}: {e}")


@asynccontextmanager
async def ai_allowance(user, bucket: str, db: Session, skip: bool = False):
    """Wrap a server-paid AI call: takes one unit before, gives it back if the block raises.
    skip=True (the user's own BYOK key is in use) counts nothing."""
    if skip:
        yield
        return
    day = await run_in_threadpool(reserve_ai_call, user, bucket, db)
    try:
        yield
    except BaseException:
        await asyncio.shield(run_in_threadpool(release_ai_call, user.id, bucket, day, db))
        raise
