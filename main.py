from fastapi import FastAPI, HTTPException, File, UploadFile, Depends
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from schema.schema import (
    UserLogin, UserSignup, CalculateATS, AuthResponse,
    DetailedATS, OptimizeResumeRequest, GeneratePDFRequest
)
from dotenv import load_dotenv
from config.database import get_db, test_connection
from config.auth import (
    get_password_hash,
    create_access_token,
    authenticate_user,
    get_current_user
)
from models.database_models import User, OptimizedResume, GenerationUsage, ParsedJDCache
from config.pdf_generator import generate_pdf_from_yaml_string
from config.email import send_verification_email
from io import BytesIO
from config.resume_functions import ats_detailed, optimize_resume, parse_jd
from models.chains import llm, res2yaml_chain
import yaml
from datetime import datetime, timedelta, date
import os
import hashlib
import secrets
from PyPDF2 import PdfReader
import logging
import logging.handlers
import uuid

def _mask_email(email: str) -> str:
    """Mask an email address for safe logging: user@example.com → u***@example.com"""
    try:
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}"
    except Exception:
        return "***"

_log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_rotating_handler = logging.handlers.RotatingFileHandler(
    "app.log", maxBytes=10 * 1_048_576, backupCount=5, encoding="utf-8"
)
_rotating_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_rotating_handler, _stream_handler])
logger = logging.getLogger("Sculpt")
load_dotenv()

app = FastAPI(title="ResumeSculpt API", version="2.0.0")

@app.middleware("http")
async def cors_and_security_middleware(request, call_next):
    if request.method == "OPTIONS":
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin":  "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, *",
                "Access-Control-Max-Age":        "86400",
            },
        )
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"]  = "*"
    # Security headers (L3)
    response.headers["X-Content-Type-Options"]       = "nosniff"
    response.headers["X-Frame-Options"]              = "DENY"
    response.headers["X-XSS-Protection"]             = "1; mode=block"
    response.headers["Referrer-Policy"]              = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"]      = "default-src 'none'"
    return response

# ==================== PLAN LIMITS ====================
PLAN_LIMITS = {
    "free": 5,
    "pro":  30,
}


# ==================== HELPERS ====================

def get_week_start(today: date = None) -> date:
    """Return the Monday of the current ISO week."""
    d = today or date.today()
    return d - timedelta(days=d.weekday())


def get_weekly_usage(user_id, db: Session) -> int:
    """Return the number of generations used this week by the user."""
    week_start = get_week_start()
    record = db.query(GenerationUsage).filter(
        GenerationUsage.user_id == user_id,
        GenerationUsage.week_start == week_start
    ).first()
    return record.count if record else 0


def increment_weekly_usage(user_id, db: Session) -> int:
    """Increment the weekly generation counter and return new count."""
    week_start = get_week_start()
    record = db.query(GenerationUsage).filter(
        GenerationUsage.user_id == user_id,
        GenerationUsage.week_start == week_start
    ).first()

    if record:
        record.count += 1
    else:
        record = GenerationUsage(
            id=uuid.uuid4(),
            user_id=user_id,
            week_start=week_start,
            count=1
        )
        db.add(record)

    db.flush()
    return record.count


def check_generation_limit(user: User, db: Session):
    """Raise 429 if the user has hit their weekly generation limit."""
    used  = get_weekly_usage(user.id, db)
    limit = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["free"])
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Weekly generation limit reached ({used}/{limit}). "
                   f"{'Upgrade to Pro for 30 generations/week.' if user.plan == 'free' else 'Limit resets every Monday.'}"
        )


def get_daily_usage(user_id, db: Session) -> int:
    """Count resume generations made today (UTC)."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end   = today_start + timedelta(days=1)
    return db.query(OptimizedResume).filter(
        OptimizedResume.user_id == user_id,
        OptimizedResume.created_at >= today_start,
        OptimizedResume.created_at <  today_end,
    ).count()


def get_monthly_usage(user_id, db: Session) -> int:
    """Count resume generations made this calendar month (UTC)."""
    month_start = datetime.combine(date.today().replace(day=1), datetime.min.time())
    return db.query(OptimizedResume).filter(
        OptimizedResume.user_id == user_id,
        OptimizedResume.created_at >= month_start,
    ).count()


def compute_resume_diff(original_yaml: str, optimized_yaml: str) -> list:
    """Semantic diff between original and optimized resume YAMLs.

    Returns a list of change dicts:
        { "severity": "positive"|"info"|"critical", "label": str, "items": list[str]|None }
    """
    changes = []
    try:
        orig = yaml.safe_load(original_yaml) or {}
        opti = yaml.safe_load(optimized_yaml) or {}
        if not isinstance(orig, dict) or not isinstance(opti, dict):
            return changes

        # 1. Education guard
        orig_edu = orig.get("education")
        opti_edu = opti.get("education")
        if orig_edu == opti_edu:
            changes.append({"severity": "info", "label": "Education preserved exactly (no changes)", "items": None})
        else:
            changes.append({"severity": "critical", "label": "Education section was altered by the model", "items": None})

        # 2. Skills diff
        def flatten_skills(data: dict) -> set:
            skills = set()
            for v in data.values():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str):
                            skills.add(item.lower())
                elif isinstance(v, str):
                    skills.add(v.lower())
            return skills

        orig_skills_section = orig.get("technical_skills") or {}
        opti_skills_section = opti.get("technical_skills") or {}
        if isinstance(orig_skills_section, dict) and isinstance(opti_skills_section, dict):
            orig_skills = flatten_skills(orig_skills_section)
            opti_skills = flatten_skills(opti_skills_section)
            added   = sorted(opti_skills - orig_skills)
            removed = sorted(orig_skills - opti_skills)
            if added:
                changes.append({"severity": "positive", "label": f"{len(added)} skill(s) added", "items": added})
            if removed:
                changes.append({"severity": "critical", "label": f"{len(removed)} skill(s) removed", "items": removed})

        # 3. Experience bullets
        orig_exp = {e.get("company", ""): e for e in (orig.get("experience") or []) if isinstance(e, dict)}
        opti_exp = {e.get("company", ""): e for e in (opti.get("experience") or []) if isinstance(e, dict)}
        for company, orig_entry in orig_exp.items():
            if company not in opti_exp:
                changes.append({"severity": "critical", "label": f"Experience entry removed: {company}", "items": None})
                continue
            opti_entry   = opti_exp[company]
            orig_bullets = set(orig_entry.get("achievements") or [])
            opti_bullets = set(opti_entry.get("achievements") or [])
            # Each changed bullet appears twice in the symmetric difference
            # (once as the removed original, once as the added enhanced version),
            # so divide by 2 to get the real count of enhanced bullets.
            changed = len(orig_bullets.symmetric_difference(opti_bullets)) // 2
            if changed:
                changes.append({"severity": "positive", "label": f"{changed} bullet(s) enhanced at {company}", "items": None})
        for company in opti_exp:
            if company not in orig_exp:
                changes.append({"severity": "info", "label": f"New experience entry added: {company}", "items": None})

        # 4. Projects
        orig_proj_names = {p.get("name", "") for p in (orig.get("projects") or []) if isinstance(p, dict)}
        opti_proj_names = {p.get("name", "") for p in (opti.get("projects") or []) if isinstance(p, dict)}
        added_projs   = sorted(opti_proj_names - orig_proj_names)
        removed_projs = sorted(orig_proj_names - opti_proj_names)
        if added_projs:
            changes.append({"severity": "positive", "label": f"{len(added_projs)} project(s) added", "items": added_projs})
        if removed_projs:
            changes.append({"severity": "critical", "label": f"{len(removed_projs)} project(s) removed", "items": removed_projs})

        # 5. Summary
        if orig.get("summary") != opti.get("summary"):
            if opti.get("summary") and not orig.get("summary"):
                changes.append({"severity": "positive", "label": "Professional summary added", "items": None})
            elif orig.get("summary") and not opti.get("summary"):
                changes.append({"severity": "critical", "label": "Professional summary removed", "items": None})
            else:
                changes.append({"severity": "info", "label": "Professional summary rewritten", "items": None})

        # 6. Top-level sections added / removed
        SKIP = {"education", "technical_skills", "experience", "projects", "summary", "name", "contact"}
        orig_sections = set(orig.keys()) - SKIP
        opti_sections = set(opti.keys()) - SKIP
        for s in sorted(opti_sections - orig_sections):
            changes.append({"severity": "info", "label": f"New section added: {s.replace('_', ' ').title()}", "items": None})
        for s in sorted(orig_sections - opti_sections):
            changes.append({"severity": "critical", "label": f"Section removed: {s.replace('_', ' ').title()}", "items": None})

    except Exception as e:
        logger.warning(f"compute_resume_diff error (non-fatal): {e}")

    return changes


def jd_hash(raw_jd: str) -> str:
    """Return a SHA-256 hex digest of the raw job description (stripped + lowercased)."""
    return hashlib.sha256(raw_jd.strip().lower().encode("utf-8")).hexdigest()


async def get_or_parse_jd(raw_jd: str, db: Session):
    """Return a cached parsedJobDescription for this JD, calling the LLM only on a cache miss.
    
    Returns:
        tuple[parsedJobDescription, str]: (parsed_jd object, cache_id string)
    """
    digest = jd_hash(raw_jd)

    # --- Cache hit ---
    cached = db.query(ParsedJDCache).filter(ParsedJDCache.jd_hash == digest).first()
    if cached:
        logger.info(f"[CACHE HIT] JD parse cache hit for hash {digest[:12]}...")
        from schema.schema import parsedJobDescription
        parsed = parsedJobDescription(
            job_title=cached.job_title or "",
            skills=cached.skills or [],
            job_description=cached.job_description,
        )
        return parsed, str(cached.id)

    # --- Cache miss — call LLM ---
    logger.info(f"[CACHE MISS] Parsing JD via LLM for hash {digest[:12]}...")
    parsed = await parse_jd(job_description=raw_jd)

    record = ParsedJDCache(
        id=uuid.uuid4(),
        jd_hash=digest,
        job_title=parsed.job_title,
        skills=parsed.skills,
        job_description=parsed.job_description,
    )
    db.add(record)
    try:
        db.commit()
        db.refresh(record)
    except IntegrityError:
        # Another concurrent request inserted the same jd_hash between our
        # cache-miss check and this INSERT (TOCTOU race). Roll back and use
        # the row that the other request committed.
        db.rollback()
        cached = db.query(ParsedJDCache).filter(ParsedJDCache.jd_hash == digest).first()
        if cached is None:
            # Should never happen, but raise a clear error if it does
            raise HTTPException(status_code=500, detail="JD cache write conflict; please retry.")
        logger.info(f"[CACHE RACE] Resolved concurrent insert for hash {digest[:12]}...")
        from schema.schema import parsedJobDescription
        parsed = parsedJobDescription(
            job_title=cached.job_title or "",
            skills=cached.skills or [],
            job_description=cached.job_description,
        )
        return parsed, str(cached.id)

    return parsed, str(record.id)


def build_auth_response(user: User, access_token: str, db: Session) -> AuthResponse:
    """Build a consistent AuthResponse including paywall fields."""
    weekly_usage  = get_weekly_usage(user.id, db)
    weekly_limit  = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["free"])
    daily_usage   = get_daily_usage(user.id, db)
    monthly_usage = get_monthly_usage(user.id, db)
    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        plan=user.plan,
        has_resume=bool(user.resume_yaml),
        resume_filename=user.resume_filename,
        email_verified=bool(user.email_verified),
        weekly_usage=weekly_usage,
        weekly_limit=weekly_limit,
        daily_usage=daily_usage,
        monthly_usage=monthly_usage,
    )


# ==================== STARTUP ====================

@app.on_event("startup")
async def startup_event():
    if test_connection():
        logger.info("[OK] Application started with database connection")
    else:
        logger.error("[WARN] Application started but database connection failed")





# ==================== BASIC ENDPOINTS ====================

@app.get('/')
def home():
    return {"response": "ResumeSculpt: AI Resume Optimization Platform"}


@app.get("/health")
def health():
    return {"status": "OK", "model_loaded": llm is not None}


# ==================== JD PARSING ENDPOINT ====================

@app.post('/parse-jd')
async def parse_job_description(
    request: CalculateATS,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Parse and cache a job description. Returns a jd_cache_id the client should
    pass to /calculate-ats-detailed and /optimize-resume to skip repeated LLM parsing."""
    if not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty.")

    parsed, cache_id = await get_or_parse_jd(request.job_desc, db)
    logger.info(f"JD parsed/cached for {current_user.email}: cache_id={cache_id}")
    return {
        "jd_cache_id": cache_id,
        "job_title":   parsed.job_title,
        "skills":      parsed.skills,
    }


# ==================== AUTHENTICATION ENDPOINTS ====================

@app.post('/auth/signup', response_model=AuthResponse)
def create_user(credentials: UserSignup, db: Session = Depends(get_db)):
    """Create a new user account and send a verification email."""
    logger.info(f"Signup attempt for email: {_mask_email(credentials.email)}")

    existing_user = db.query(User).filter(User.email == credentials.email).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    # Generate a secure one-time verification token
    verification_token = secrets.token_urlsafe(32)

    new_user = User(
        id=uuid.uuid4(),
        email=credentials.email,
        password_hash=get_password_hash(credentials.password),
        full_name=credentials.full_name,
        plan="free",
        email_verified=False,
        email_verification_token=verification_token,
        email_verification_sent_at=datetime.utcnow(),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Send verification email (non-blocking — failure doesn't break signup)
    try:
        send_verification_email(new_user.email, new_user.full_name, verification_token)
    except Exception as e:
        logger.warning(f"[EMAIL] Verification email failed for {_mask_email(new_user.email)}: {e}")

    access_token = create_access_token(
        data={"sub": str(new_user.id)},
        expires_delta=timedelta(hours=24)
    )
    logger.info(f"[OK] User created: {_mask_email(new_user.email)}")
    return build_auth_response(new_user, access_token, db)


@app.post('/auth/login', response_model=AuthResponse)
def login_user(credentials: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user and return JWT token."""
    logger.info(f"Login attempt for: {_mask_email(credentials.email)}")

    user = authenticate_user(db, credentials.email, credentials.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(hours=24)
    )
    logger.info(f"[OK] User logged in: {_mask_email(user.email)}")
    return build_auth_response(user, access_token, db)


@app.get('/auth/me')
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current authenticated user's profile including paywall status."""
    weekly_usage  = get_weekly_usage(current_user.id, db)
    weekly_limit  = PLAN_LIMITS.get(current_user.plan, PLAN_LIMITS["free"])
    daily_usage   = get_daily_usage(current_user.id, db)
    monthly_usage = get_monthly_usage(current_user.id, db)
    return {
        "user_id":            str(current_user.id),
        "email":              current_user.email,
        "full_name":          current_user.full_name,
        "plan":               current_user.plan,
        "has_resume":         bool(current_user.resume_yaml),
        "resume_filename":    current_user.resume_filename,
        "resume_uploaded_at": current_user.resume_uploaded_at.isoformat() if current_user.resume_uploaded_at else None,
        "email_verified":     bool(current_user.email_verified),
        "weekly_usage":       weekly_usage,
        "weekly_limit":       weekly_limit,
        "daily_usage":        daily_usage,
        "monthly_usage":      monthly_usage,
        "created_at":         current_user.created_at.isoformat() if current_user.created_at else None,
    }


@app.delete('/auth/me')
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permanently delete the authenticated user's account and all associated data.

    Deletes:
    - The user record itself
    - All OptimizedResume rows (via CASCADE)
    - All GenerationUsage rows (via CASCADE)

    This action is irreversible (GDPR right to erasure).
    """
    masked = _mask_email(current_user.email)
    logger.info(f"[DELETE ACCOUNT] Request for {masked}")

    db.delete(current_user)
    db.commit()

    logger.info(f"[DELETE ACCOUNT] Account permanently deleted: {masked}")
    return {"message": "Your account and all associated data have been permanently deleted."}


@app.post('/auth/refresh', response_model=AuthResponse)
def refresh_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Issue a fresh JWT for an authenticated user.
    The existing token must still be valid (not expired).
    Call proactively when < 24h remain to keep sessions alive.
    """
    weekly_usage  = get_weekly_usage(current_user.id, db)
    weekly_limit  = PLAN_LIMITS.get(current_user.plan, PLAN_LIMITS["free"])
    daily_usage   = get_daily_usage(current_user.id, db)
    monthly_usage = get_monthly_usage(current_user.id, db)

    new_token = create_access_token(data={"sub": str(current_user.id)})
    logger.info(f"[REFRESH] Token refreshed for {current_user.email}")

    return {
        "access_token":    new_token,
        "token_type":      "bearer",
        "user_id":         str(current_user.id),
        "email":           current_user.email,
        "full_name":       current_user.full_name,
        "plan":            current_user.plan,
        "has_resume":      bool(current_user.resume_yaml),
        "resume_filename": current_user.resume_filename,
        "email_verified":  bool(current_user.email_verified),
        "weekly_usage":    weekly_usage,
        "weekly_limit":    weekly_limit,
        "daily_usage":     daily_usage,
        "monthly_usage":   monthly_usage,
    }


@app.post('/auth/google', response_model=AuthResponse)
async def google_auth(payload: dict, db: Session = Depends(get_db)):
    """
    Verify a Google access token (from chrome.identity.getAuthToken) and
    return a ResumeSculpt session JWT.
    The extension sends: { access_token }
    No client secret needed — Chrome Extension OAuth type handles auth on Google's side.
    """
    import httpx

    google_access_token = payload.get("access_token")
    if not google_access_token:
        raise HTTPException(status_code=400, detail="access_token is required.")

    # ── Step 1: Verify token and fetch user profile from Google ─────────────
    async with httpx.AsyncClient() as client:
        profile_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if profile_res.status_code != 200:
        logger.error(f"[GOOGLE] Token verification failed: {profile_res.text}")
        raise HTTPException(status_code=401, detail="Invalid or expired Google access token.")

    profile         = profile_res.json()
    google_email    = profile.get("email")
    google_name     = profile.get("name")
    google_verified = profile.get("verified_email", False)

    if not google_email:
        raise HTTPException(status_code=400, detail="Could not retrieve email from Google.")

    # ── Step 2: Find or create user ─────────────────────────────────────────
    user = db.query(User).filter(User.email == google_email).first()

    if not user:
        # New user — set a random unusable password (OAuth users sign in via Google)
        user = User(
            id=uuid.uuid4(),
            email=google_email,
            password_hash=get_password_hash(secrets.token_hex(32)),
            full_name=google_name,
            plan="free",
            email_verified=google_verified,  # Google already verified their email
            email_verification_token=None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"[GOOGLE] New user created via OAuth: {google_email}")
    else:
        # Existing user — sync verified status from Google if not already set
        if google_verified and not user.email_verified:
            user.email_verified = True
            db.commit()
        logger.info(f"[GOOGLE] Existing user signed in via OAuth: {google_email}")

    # ── Step 3: Issue our own JWT ────────────────────────────────────────────
    jwt_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(hours=24)
    )
    return build_auth_response(user, jwt_token, db)


@app.get('/auth/verify-email')
def verify_email(token: str, db: Session = Depends(get_db)):
    """
    Verify a user's email address using the one-time token sent on signup.
    Token is valid for 24 hours.
    """
    if not token:
        raise HTTPException(status_code=400, detail="Verification token is required.")

    user = db.query(User).filter(User.email_verification_token == token).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link.")

    # Check 24-hour expiry
    if user.email_verification_sent_at:
        age = datetime.utcnow() - user.email_verification_sent_at.replace(tzinfo=None)
        if age.total_seconds() > 86_400:  # 24 hours
            raise HTTPException(
                status_code=400,
                detail="Verification link has expired. Please request a new one."
            )

    if user.email_verified:
        return {"message": "Email already verified. You can close this page."}

    user.email_verified             = True
    user.email_verification_token   = None  # Invalidate token after use
    db.commit()

    logger.info(f"[OK] Email verified for {user.email}")
    # Return a simple success page the browser can display
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>Email Verified — ResumeSculpt</title></head>
    <body style="font-family:'Segoe UI',Arial,sans-serif;background:#0f1117;
                 display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">
      <div style="background:#1a1d2e;border-radius:16px;padding:48px;text-align:center;max-width:420px;">
        <div style="font-size:56px;">✅</div>
        <h1 style="color:#e2e8f0;margin:16px 0 8px;">Email Verified!</h1>
        <p style="color:#94a3b8;margin:0 0 32px;line-height:1.6;">
          Your account is now fully active.<br>You can close this tab and return to the extension.
        </p>
        <a href="#" onclick="window.close()"
           style="background:linear-gradient(135deg,#6c63ff,#a78bfa);
                  color:#fff;padding:12px 28px;border-radius:8px;
                  text-decoration:none;font-weight:600;">Close Tab</a>
      </div>
    </body>
    </html>
    """, status_code=200)


@app.post('/auth/resend-verification')
def resend_verification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resend the verification email. Rate-limited to once every 60 seconds."""
    if current_user.email_verified:
        raise HTTPException(status_code=400, detail="Your email is already verified.")

    # Throttle: don't resend if last email was sent less than 60 seconds ago
    if current_user.email_verification_sent_at:
        age = datetime.utcnow() - current_user.email_verification_sent_at.replace(tzinfo=None)
        if age.total_seconds() < 60:
            wait = int(60 - age.total_seconds())
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {wait} seconds before requesting another email."
            )

    token = secrets.token_urlsafe(32)
    current_user.email_verification_token   = token
    current_user.email_verification_sent_at = datetime.utcnow()
    db.commit()

    try:
        send_verification_email(current_user.email, current_user.full_name, token)
    except Exception as e:
        logger.error(f"[EMAIL] Resend failed for {current_user.email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to send verification email. Please try again.")

    return {"message": "Verification email sent. Please check your inbox."}


# ==================== RESUME MANAGEMENT ENDPOINTS ====================

@app.post('/upload-resume')
async def upload_resume(
    file: UploadFile = File(..., description="Resume PDF file"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload and parse user's resume. Replaces any previously uploaded resume."""
    logger.info(f"Resume upload for user: {current_user.email}")

    # Block unverified users from using LLM-backed features
    if not current_user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address before uploading a resume. Check your inbox for the verification link."
        )

    # Validate file type via content-type header
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are allowed. Got: {file.content_type}"
        )

    # Validate file size (2 MB limit) — checked from header first (fast path)
    PDF_MAX_BYTES = 2 * 1_048_576  # 2 MB
    if file.size and int(file.size) > PDF_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum 2 MB allowed."
        )

    # Extract text from PDF
    try:
        pdf_bytes = await file.read()

        # Re-check size after read (content-type / size header can be spoofed)
        if len(pdf_bytes) > PDF_MAX_BYTES:
            raise HTTPException(status_code=400, detail="File too large. Maximum 2 MB allowed.")

        # Magic-byte check — PDFs always start with "%PDF"
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="Invalid file: not a PDF.")

        reader = PdfReader(BytesIO(pdf_bytes))

        # Guard against malformed / adversarial PDFs with absurd page counts
        if len(reader.pages) > 50:
            raise HTTPException(status_code=400, detail="PDF has too many pages (max 50).")

        resume_content = "".join(page.extract_text() or "" for page in reader.pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PDF read error for {current_user.id}: {e}")
        raise HTTPException(status_code=400, detail="Failed to read PDF. It may be corrupted or password-protected.")

    if not resume_content.strip():
        raise HTTPException(status_code=400, detail="No text found in PDF. Please ensure the resume is not a scanned image.")

    # Parse resume to YAML via AI
    response = await res2yaml_chain.ainvoke(input={"resume_content": resume_content})
    resume_yaml = response.content

    # Strip markdown code fences if present
    if "```" in resume_yaml:
        resume_yaml = resume_yaml.split("```yaml")[-1] if "```yaml" in resume_yaml else resume_yaml.split("```")[-1]
        resume_yaml = resume_yaml.split("```")[0].strip()

    # Save debug copy locally (only when DEBUG_DUMP=true in .env)
    if os.getenv("DEBUG_DUMP", "false").lower() == "true":
        debug_folder = "debug_resumes"
        os.makedirs(debug_folder, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_path = os.path.join(debug_folder, f"resume_{current_user.id}_{timestamp}.yaml")
        try:
            yaml_data = yaml.safe_load(resume_yaml)
            with open(debug_path, 'w', encoding='utf-8') as f:
                yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except yaml.YAMLError:
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(resume_yaml)

    # Store resume directly on the user record (replaces any previous resume)
    current_user.resume_yaml        = resume_yaml
    current_user.resume_filename    = file.filename
    current_user.resume_uploaded_at = datetime.utcnow()
    db.commit()
    db.refresh(current_user)

    logger.info(f"[OK] Resume processed for {current_user.email}")
    return {
        "message":  "Resume uploaded and parsed successfully",
        "filename": file.filename,
    }


@app.get('/my-resume')
async def get_my_resume(
    current_user: User = Depends(get_current_user)
):
    """Get the current user's active resume."""
    if not current_user.resume_yaml:
        raise HTTPException(status_code=404, detail="No resume found. Please upload a resume first.")

    return {
        "filename":           current_user.resume_filename,
        "resume_yaml":        current_user.resume_yaml,
        "resume_uploaded_at": current_user.resume_uploaded_at.isoformat() if current_user.resume_uploaded_at else None,
    }


# ==================== ATS & OPTIMIZATION ENDPOINTS ====================

@app.post('/calculate-ats-detailed', response_model=DetailedATS)
async def calculate_ats_detailed(
    request: CalculateATS,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Calculate a detailed ATS score for the user's resume against a job description.
    
    If `jd_cache_id` is provided in the request body, the pre-parsed JD is reused
    from DB and no LLM parse call is made.
    """
    logger.info(f"ATS calculation for user: {current_user.email}")

    # Block unverified users
    if not current_user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address before using ATS analysis. Check your inbox for the verification link."
        )

    if not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty.")

    if not current_user.resume_yaml:
        raise HTTPException(status_code=404, detail="No resume found. Please upload a resume first.")

    # Use cached parse if the client provided a jd_cache_id
    if request.jd_cache_id:
        cached = db.query(ParsedJDCache).filter(
            ParsedJDCache.id == request.jd_cache_id
        ).first()
        if cached:
            from schema.schema import parsedJobDescription
            parsed_jd = parsedJobDescription(
                job_title=cached.job_title or "",
                skills=cached.skills or [],
                job_description=cached.job_description,
            )
            logger.info(f"[CACHE HIT] Reused parsed JD for ATS (cache_id={request.jd_cache_id})")
        else:
            logger.warning(f"jd_cache_id {request.jd_cache_id} not found — falling back to live parse")
            parsed_jd, _ = await get_or_parse_jd(request.job_desc, db)
    else:
        parsed_jd, _ = await get_or_parse_jd(request.job_desc, db)

    try:
        result = await ats_detailed(current_user.resume_yaml, parsed_jd)
        logger.info(f"ATS score for {current_user.email}: {result.overall_score}")
        return result
    except Exception as e:
        logger.error(f"ATS error for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Unable to calculate ATS score. Please try again.")


@app.post('/optimize-resume')
async def optimize_resume_endpoint(
    request: OptimizeResumeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Optimize resume for a job description.
    
    Accepts optional `jd_cache_id` (skip LLM JD parse) and `original_ats_score`
    (skip re-running the original ATS calculation — already done by /calculate-ats-detailed).
    
    Enforces weekly generation limits based on the user's plan:
      - free: 5 generations / week
      - pro:  30 generations / week
    """
    logger.info(f"Resume optimization for user: {current_user.email} (plan: {current_user.plan})")

    # Block unverified users
    if not current_user.email_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email address before generating optimized resumes. Check your inbox for the verification link."
        )

    if not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty.")

    if not current_user.resume_yaml:
        raise HTTPException(status_code=404, detail="No resume found. Please upload a resume first.")

    # ---- Paywall check ----
    check_generation_limit(current_user, db)

    # ---- Resolve parsed JD (cache-first) ----
    if request.jd_cache_id:
        cached = db.query(ParsedJDCache).filter(
            ParsedJDCache.id == request.jd_cache_id
        ).first()
        if cached:
            from schema.schema import parsedJobDescription
            parsed_jd = parsedJobDescription(
                job_title=cached.job_title or "",
                skills=cached.skills or [],
                job_description=cached.job_description,
            )
            logger.info(f"[CACHE HIT] Reused parsed JD for optimize (cache_id={request.jd_cache_id})")
        else:
            logger.warning(f"jd_cache_id {request.jd_cache_id} not found — falling back to live parse")
            parsed_jd, _ = await get_or_parse_jd(request.job_desc, db)
    else:
        parsed_jd, _ = await get_or_parse_jd(request.job_desc, db)

    try:
        # ---- Original ATS score (skip if already computed by the Analyze step) ----
        if request.original_ats_score is not None:
            logger.info(f"Using pre-computed original ATS score: {request.original_ats_score} for {current_user.email}")
            # Build a minimal object so we can still compute keywords_added diff
            original_ats = None
            original_score_value = request.original_ats_score
        else:
            original_ats = await ats_detailed(current_user.resume_yaml, parsed_jd)
            original_score_value = original_ats.overall_score
            logger.info(f"Original ATS for {current_user.email}: {original_score_value}")

        # ---- Generate the optimized resume ----
        optimized_yaml = await optimize_resume(
            resume_content=current_user.resume_yaml,
            job_description=parsed_jd,
            addons=""
        )

        # Strip markdown fences
        if "```" in optimized_yaml:
            optimized_yaml = optimized_yaml.split("```yaml")[-1] if "```yaml" in optimized_yaml else optimized_yaml.split("```")[-1]
            optimized_yaml = optimized_yaml.split("```")[0].strip()

        # ---- Score the optimized resume ----
        optimized_ats = await ats_detailed(optimized_yaml, parsed_jd)
        logger.info(f"Optimized ATS for {current_user.email}: {optimized_ats.overall_score}")

        # ---- Build improvement metadata ----
        if original_ats is not None:
            keywords_added = list(
                set(optimized_ats.keyword_analysis.matched_keywords) -
                set(original_ats.keyword_analysis.matched_keywords)
            )
        else:
            # We didn't re-run the original ATS — diff is unavailable, return empty list
            keywords_added = []

        improvements_made = [
            f"Score improved from {original_score_value:.1f} to {optimized_ats.overall_score:.1f}",
            f"Added {len(keywords_added)} new matching keywords",
        ]

        # Save debug copy (only when DEBUG_DUMP=true in .env)
        if os.getenv("DEBUG_DUMP", "false").lower() == "true":
            folder = "optimized_resumes"
            os.makedirs(folder, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(folder, f"optimized_{current_user.id}_{ts}.yaml")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(optimized_yaml)

        # Persist to database
        record = OptimizedResume(
            id=uuid.uuid4(),
            user_id=current_user.id,
            job_description=request.job_desc,
            job_title=parsed_jd.job_title,
            original_ats_score=original_score_value,
            optimized_ats_score=optimized_ats.overall_score,
            score_improvement=optimized_ats.overall_score - original_score_value,
            match_level=optimized_ats.match_level,
            optimized_yaml=optimized_yaml,
            keywords_added=keywords_added,
            improvements_made=improvements_made,
        )
        db.add(record)

        # ---- Increment weekly usage ----
        new_count = increment_weekly_usage(current_user.id, db)
        db.commit()

        weekly_limit = PLAN_LIMITS.get(current_user.plan, PLAN_LIMITS["free"])
        logger.info(f"[OK] Optimization complete for {current_user.email}. Weekly usage: {new_count}/{weekly_limit}")

        # ---- Compute changelog diff ----
        resume_changes = compute_resume_diff(current_user.resume_yaml, optimized_yaml)

        # ---- Usage stats for the meter ----
        daily_usage   = get_daily_usage(current_user.id, db)
        monthly_usage = get_monthly_usage(current_user.id, db)

        return {
            "message":                    "Resume optimized successfully",
            "original_score":             round(original_score_value, 2),
            "optimized_score":            round(optimized_ats.overall_score, 2),
            "score_improvement":          round(optimized_ats.overall_score - original_score_value, 2),
            "match_level":                optimized_ats.match_level,
            "improvements_made":          improvements_made,
            "keywords_added":             keywords_added,
            "critical_improvements_remaining": optimized_ats.critical_improvements,
            "optimized_resume_yaml":      optimized_yaml,
            "weekly_usage":               new_count,
            "weekly_limit":               weekly_limit,
            "resume_changes":             resume_changes,
            "daily_usage":                daily_usage,
            "monthly_usage":              monthly_usage,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Optimization error for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Unable to optimize resume. Please try again.")


# ==================== HISTORY ENDPOINT ====================

@app.get('/my-optimizations')
async def get_my_optimizations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return the user's past resume optimizations (newest first)."""
    records = (
        db.query(OptimizedResume)
        .filter(OptimizedResume.user_id == current_user.id)
        .order_by(OptimizedResume.created_at.desc())
        .all()
    )
    return [
        {
            "id":                   str(r.id),
            "job_title":            r.job_title,
            "original_ats_score":   r.original_ats_score,
            "optimized_ats_score":  r.optimized_ats_score,
            "score_improvement":    r.score_improvement,
            "match_level":          r.match_level,
            "keywords_added":       r.keywords_added,
            "improvements_made":    r.improvements_made,
            "created_at":           r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]

@app.get('/my-optimizations/{record_id}/pdf')
async def download_optimization_pdf(
    record_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-download a past optimized resume as a PDF by its record ID."""
    try:
        rid = uuid.UUID(record_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid optimization ID.")

    record = (
        db.query(OptimizedResume)
        .filter(OptimizedResume.id == rid, OptimizedResume.user_id == current_user.id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Optimization not found.")

    try:
        pdf_bytes = generate_pdf_from_yaml_string(record.optimized_yaml)
    except Exception as e:
        logger.error(f"PDF re-download error for record {record_id}: {e}")
        raise HTTPException(status_code=500, detail="PDF generation failed.")

    safe_title = (record.job_title or "optimized_resume").replace(" ", "_")[:40]
    filename = f"resume_{safe_title}.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ==================== PDF GENERATION ENDPOINT ====================

@app.post('/generate-pdf')
async def generate_pdf_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    body: GeneratePDFRequest = None,
):
    """
    Generate a PDF from a resume YAML string and stream it back.

    Body (optional JSON):
        { "resume_yaml": "<yaml string>" }

    If resume_yaml is omitted, the user's stored base resume is used.
    """
    # Resolve which YAML to render
    resume_yaml = None
    if body and body.resume_yaml:
        resume_yaml = body.resume_yaml

    if not resume_yaml:
        resume_yaml = current_user.resume_yaml


    if not resume_yaml:
        raise HTTPException(
            status_code=404,
            detail="No resume found. Please upload a resume first."
        )

    try:
        pdf_bytes = generate_pdf_from_yaml_string(resume_yaml)
    except Exception as e:
        logger.error(f"PDF generation error for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    logger.info(f"[OK] PDF generated for {current_user.email} ({len(pdf_bytes):,} bytes)")

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="optimized_resume.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ==================== ADMIN ENDPOINTS ====================

@app.delete("/admin/cleanup")
def admin_cleanup(
    token: str,
    older_than_days: int = 7,
):
    """
    Delete debug dump files older than `older_than_days` days from
    `debug_resumes/` and `optimized_resumes/`.

    Protected by a static secret token set via ADMIN_CLEANUP_TOKEN env var.
    Only relevant when DEBUG_DUMP=true has been used to generate dump files.

    Args:
        token: Must match the ADMIN_CLEANUP_TOKEN environment variable.
        older_than_days: Files older than this many days are deleted (default: 7).
    """
    expected = os.getenv("ADMIN_CLEANUP_TOKEN")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing cleanup token.")

    if older_than_days < 1:
        raise HTTPException(status_code=400, detail="older_than_days must be at least 1.")

    cutoff = datetime.now().timestamp() - (older_than_days * 86_400)
    deleted = []
    errors  = []

    for folder in ("debug_resumes", "optimized_resumes"):
        if not os.path.isdir(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    deleted.append(f"{folder}/{fname}")
            except Exception as e:
                errors.append(f"{folder}/{fname}: {e}")

    logger.info(f"[CLEANUP] Deleted {len(deleted)} debug file(s) older than {older_than_days}d. Errors: {len(errors)}")
    return {
        "deleted_count": len(deleted),
        "deleted_files": deleted,
        "errors":        errors,
    }


