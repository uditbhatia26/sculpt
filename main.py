from fastapi import FastAPI, HTTPException, File, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from schema.schema import (
    UserLogin, UserSignup, CalculateATS, AuthResponse,
    DetailedATS, OptimizeResumeRequest
)
from dotenv import load_dotenv
from config.database import get_db, test_connection
from config.auth import (
    get_password_hash,
    create_access_token,
    authenticate_user,
    get_current_user
)
from models.database_models import User, OptimizedResume, GenerationUsage
from io import BytesIO
from config.resume_functions import ats_detailed, optimize_resume, parse_jd
from models.chains import llm, res2yaml_chain
import yaml
from datetime import datetime, timedelta, date
import os
from PyPDF2 import PdfReader
import logging
import uuid

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Sculpt")
load_dotenv()

app = FastAPI(title="ResumeSculpt API", version="2.0.0")

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


def build_auth_response(user: User, access_token: str, db: Session) -> AuthResponse:
    """Build a consistent AuthResponse including paywall fields."""
    weekly_usage = get_weekly_usage(user.id, db)
    weekly_limit = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["free"])
    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        plan=user.plan,
        has_resume=bool(user.resume_yaml),
        weekly_usage=weekly_usage,
        weekly_limit=weekly_limit,
    )


# ==================== STARTUP ====================

@app.on_event("startup")
async def startup_event():
    if test_connection():
        logger.info("[OK] Application started with database connection")
    else:
        logger.error("[WARN] Application started but database connection failed")


# ==================== CORS ====================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "chrome-extension://*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== BASIC ENDPOINTS ====================

@app.get('/')
def home():
    return {"response": "ResumeSculpt: AI Resume Optimization Platform"}


@app.get("/health")
def health():
    return {"status": "OK", "model_loaded": llm is not None}


# ==================== AUTHENTICATION ENDPOINTS ====================

@app.post('/auth/signup', response_model=AuthResponse)
def create_user(credentials: UserSignup, db: Session = Depends(get_db)):
    """Create a new user account."""
    logger.info(f"Signup attempt for email: {credentials.email}")

    existing_user = db.query(User).filter(User.email == credentials.email).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="User with this email already exists")

    new_user = User(
        id=uuid.uuid4(),
        email=credentials.email,
        password_hash=get_password_hash(credentials.password),
        full_name=credentials.full_name,
        plan="free",
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = create_access_token(
        data={"sub": str(new_user.id)},
        expires_delta=timedelta(days=7)
    )
    logger.info(f"✅ User created: {new_user.email}")
    return build_auth_response(new_user, access_token, db)


@app.post('/auth/login', response_model=AuthResponse)
def login_user(credentials: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user and return JWT token."""
    logger.info(f"Login attempt for: {credentials.email}")

    user = authenticate_user(db, credentials.email, credentials.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(days=7)
    )
    logger.info(f"✅ User logged in: {user.email}")
    return build_auth_response(user, access_token, db)


@app.get('/auth/me')
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current authenticated user's profile including paywall status."""
    weekly_usage = get_weekly_usage(current_user.id, db)
    weekly_limit = PLAN_LIMITS.get(current_user.plan, PLAN_LIMITS["free"])
    return {
        "user_id":            str(current_user.id),
        "email":              current_user.email,
        "full_name":          current_user.full_name,
        "plan":               current_user.plan,
        "has_resume":         bool(current_user.resume_yaml),
        "resume_filename":    current_user.resume_filename,
        "resume_uploaded_at": current_user.resume_uploaded_at.isoformat() if current_user.resume_uploaded_at else None,
        "weekly_usage":       weekly_usage,
        "weekly_limit":       weekly_limit,
        "created_at":         current_user.created_at.isoformat() if current_user.created_at else None,
    }


# ==================== RESUME MANAGEMENT ENDPOINTS ====================

@app.post('/upload-resume')
async def upload_resume(
    file: UploadFile = File(..., description="Resume PDF file"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload and parse user's resume. Replaces any previously uploaded resume."""
    logger.info(f"Resume upload for user: {current_user.email}")

    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are allowed. Got: {file.content_type}"
        )

    # Validate file size (2 MB limit)
    if file.size and (int(file.size) / 1_048_576) > 2:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum 2 MB allowed. Got: {(int(file.size) / 1_048_576):.2f} MB"
        )

    # Extract text from PDF
    try:
        pdf_bytes = await file.read()
        reader = PdfReader(BytesIO(pdf_bytes))
        resume_content = "".join(page.extract_text() or "" for page in reader.pages)
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

    # Save debug copy locally
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

    logger.info(f"✅ Resume processed for {current_user.email}")
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
    """Calculate a detailed ATS score for the user's resume against a job description."""
    logger.info(f"ATS calculation for user: {current_user.email}")

    if not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty.")

    if not current_user.resume_yaml:
        raise HTTPException(status_code=404, detail="No resume found. Please upload a resume first.")

    parsed_jd = await parse_jd(job_description=request.job_desc)

    try:
        result = await ats_detailed(current_user.resume_yaml, parsed_jd)
        logger.info(f"ATS score for {current_user.email}: {result.overall_score}")
        return result
    except Exception as e:
        logger.error(f"ATS error for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Unable to calculate ATS: {e}")


@app.post('/optimize-resume')
async def optimize_resume_endpoint(
    request: OptimizeResumeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Optimize resume for a job description.
    Enforces weekly generation limits based on the user's plan:
      - free: 5 generations / week
      - pro:  30 generations / week
    """
    logger.info(f"Resume optimization for user: {current_user.email} (plan: {current_user.plan})")

    if not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty.")

    if not current_user.resume_yaml:
        raise HTTPException(status_code=404, detail="No resume found. Please upload a resume first.")

    # ---- Paywall check ----
    check_generation_limit(current_user, db)

    parsed_jd = await parse_jd(job_description=request.job_desc)

    try:
        # Score the original resume
        original_ats = await ats_detailed(current_user.resume_yaml, parsed_jd)
        logger.info(f"Original ATS for {current_user.email}: {original_ats.overall_score}")

        # Generate the optimized version
        optimized_yaml = await optimize_resume(
            resume_content=current_user.resume_yaml,
            job_description=parsed_jd,
            addons=""
        )

        # Strip markdown fences
        if "```" in optimized_yaml:
            optimized_yaml = optimized_yaml.split("```yaml")[-1] if "```yaml" in optimized_yaml else optimized_yaml.split("```")[-1]
            optimized_yaml = optimized_yaml.split("```")[0].strip()

        # Score the optimized resume
        optimized_ats = await ats_detailed(optimized_yaml, parsed_jd)
        logger.info(f"Optimized ATS for {current_user.email}: {optimized_ats.overall_score}")

        # Build improvement metadata
        keywords_added    = list(set(optimized_ats.keyword_analysis.matched_keywords) - set(original_ats.keyword_analysis.matched_keywords))
        improvements_made = [
            f"Score improved from {original_ats.overall_score:.1f} to {optimized_ats.overall_score:.1f}",
            f"Added {len(keywords_added)} new matching keywords",
        ]

        # Save debug copy
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
            original_ats_score=original_ats.overall_score,
            optimized_ats_score=optimized_ats.overall_score,
            score_improvement=optimized_ats.overall_score - original_ats.overall_score,
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
        logger.info(f"✅ Optimization complete for {current_user.email}. Weekly usage: {new_count}/{weekly_limit}")

        return {
            "message":                    "Resume optimized successfully",
            "original_score":             round(original_ats.overall_score, 2),
            "optimized_score":            round(optimized_ats.overall_score, 2),
            "score_improvement":          round(optimized_ats.overall_score - original_ats.overall_score, 2),
            "match_level":                optimized_ats.match_level,
            "improvements_made":          improvements_made,
            "keywords_added":             keywords_added,
            "critical_improvements_remaining": optimized_ats.critical_improvements,
            "optimized_resume_yaml":      optimized_yaml,
            "weekly_usage":               new_count,
            "weekly_limit":               weekly_limit,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Optimization error for {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Unable to optimize resume: {e}")


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