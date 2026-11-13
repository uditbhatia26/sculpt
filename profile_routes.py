"""
Profile pool and onboarding.

Onboarding (on the website):  resume -> projects -> achievements -> done
Profile pool:                 projects (Chisel from GitHub, or typed in) and achievements.
                              The optimizer may draw on approved items when one fits the job.
Chisel:                       three resume bullets written from a GitHub repository the user
                              contributed to. Each plan has a number of saved slots and a number
                              of adds per allowance period (the period starts with the first add
                              and lasts 30 days). Any project can be removed at any time; that
                              frees its slot but does not give the add back. Three AI edits per
                              Chisel project.
Hand-added projects:          the user writes them; no AI edits. Free: one at a time (replace it
                              whenever). Pro: no visible limit (a quiet safety cap).
"""
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from config import github_client as gh
from config.auth import create_access_token, decode_token, get_current_user
from config.database import get_db
from config.rate_limit import rate_limit
from models.database_models import ProfileAchievement, ProfileProject, User
from models.llm_factory import build_server_llm
from models.profile_chains import build_chisel_chain, build_chisel_revise_chain, build_refine_chain

logger = logging.getLogger("Sculpt")
router = APIRouter()

ONBOARDING_STEPS = ("resume", "projects", "achievements", "done")

# Per plan: Chisel projects saved at one time, and Chisel adds per allowance period
CHISEL_LIMITS = {"free": {"slots": 1, "adds": 1}, "pro": {"slots": 5, "adds": 5}}
CHISEL_PERIOD_DAYS = 30
CHISEL_AI_EDITS = 3                       # "improve these bullets" rounds per Chisel project
# Hand-added projects: the free plan allows one; Pro has no visible limit, only this quiet cap
MANUAL_PROJECT_LIMITS = {"free": 1, "pro": None}
MANUAL_PROJECT_SAFETY_CAP = 20
MAX_ACHIEVEMENTS = 25
ACHIEVEMENT_CATEGORIES = (
    "hackathon", "leadership", "sports", "event", "award", "certification", "volunteering", "other",
)
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def dashboard_url() -> str:
    return os.getenv("DASHBOARD_URL", "http://localhost:5173").rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- onboarding state

def onboarding_state(user: User) -> dict:
    """What the clients need to know. `setup_required` is what locks the extension: setup
    isn't finished and the account isn't one that predates onboarding."""
    complete = isinstance(user.onboarding_completed_at, datetime)
    step = user.onboarding_step if isinstance(user.onboarding_step, str) else "resume"
    return {
        "onboarding_step": "done" if complete else step,
        "onboarding_complete": complete,
        "setup_required": not complete and user.onboarding_grandfathered is not True,
    }


# ---------------------------------------------------------------- serializers

def project_out(p: ProfileProject) -> dict:
    is_github = p.source == "github"
    return {
        "id": str(p.id),
        "source": p.source,
        "name": p.name,
        "summary": p.summary or "",
        "tech_stack": p.tech_stack or [],
        "bullets": p.bullets or [],
        "status": p.status,
        "repo_full_name": p.repo_full_name,
        "repo_url": p.repo_url,
        "repo_private": bool(p.repo_private),
        "role": p.role or "",
        "facts": p.facts or "",
        "ai_edits_left": max(0, CHISEL_AI_EDITS - (p.ai_edits_used or 0)) if is_github else 0,
    }


def achievement_out(a: ProfileAchievement) -> dict:
    return {"id": str(a.id), "category": a.category, "text": a.text, "original_text": a.original_text or ""}


def _plan(user: User) -> str:
    return user.plan if user.plan in CHISEL_LIMITS else "free"


def chisel_period(user: User) -> tuple:
    """(adds used, period end) for the current allowance period. A period starts with the
    first add after the previous one ended, so (0, None) means no period is running."""
    start = _aware(user.chisel_period_start)
    if not start or _now() >= start + timedelta(days=CHISEL_PERIOD_DAYS):
        return 0, None
    return (user.chisel_period_adds or 0), start + timedelta(days=CHISEL_PERIOD_DAYS)


def count_chisel_add(user: User) -> None:
    """Record one add, starting a new period if none is running. Call inside the commit
    that saves the project, so a failed generation is never counted."""
    used, ends = chisel_period(user)
    if ends is None:
        user.chisel_period_start = _now()
        used = 0
    user.chisel_period_adds = used + 1
    user.chisel_generations_used = (user.chisel_generations_used or 0) + 1


def chisel_status(user: User, db: Session, saved: int | None = None) -> dict:
    """Whether the user can add another Chisel project right now, and if not, why.
    Pass `saved` (the user's GitHub project count) when the caller already has the projects,
    to skip a database round trip."""
    plan = _plan(user)
    limits = CHISEL_LIMITS[plan]
    if saved is None:
        saved = db.query(ProfileProject).filter(
            ProfileProject.user_id == user.id, ProfileProject.source == "github"
        ).count()
    used, ends = chisel_period(user)
    adds_left = max(0, limits["adds"] - used)
    when = f"{ends.day} {ends.strftime('%b %Y')}" if ends else ""   # "9 Dec 2026", as the website writes it

    can_add, reason = True, ""
    if adds_left == 0:
        can_add = False
        reason = (f"You've used your {limits['adds']} GitHub "
                  f"{'add' if limits['adds'] == 1 else 'adds'} for this period. New adds on {when}.")
    elif saved >= limits["slots"]:
        can_add = False
        reason = (f"You have {saved} of {limits['slots']} GitHub "
                  f"{'project' if limits['slots'] == 1 else 'projects'} saved. Remove one to add another.")

    return {
        "plan": plan,
        "slots": limits["slots"],
        "used": saved,
        "adds_per_period": limits["adds"],
        "adds_left": adds_left,
        "period_ends_at": ends.isoformat() if ends else None,
        "period_days": CHISEL_PERIOD_DAYS,
        "can_add": can_add,
        "reason": reason,
        "ai_edits_per_project": CHISEL_AI_EDITS,
    }


def manual_status(user: User, db: Session, saved: int | None = None) -> dict:
    """Hand-added projects: the visible limit (None on Pro) and whether one more fits.
    Pass `saved` (the user's hand-added count) when the caller already has the projects."""
    limit = MANUAL_PROJECT_LIMITS[_plan(user)]
    if saved is None:
        saved = db.query(ProfileProject).filter(
            ProfileProject.user_id == user.id, ProfileProject.source == "manual"
        ).count()
    cap = limit if limit is not None else MANUAL_PROJECT_SAFETY_CAP
    return {"limit": limit, "used": saved, "can_add": saved < cap}


def build_profile_addons(user_id, db: Session) -> str:
    """The approved profile pool as text for the optimizer's ADDONS slot. Empty if there is none."""
    projects = db.query(ProfileProject).filter(
        ProfileProject.user_id == user_id, ProfileProject.status == "approved"
    ).order_by(ProfileProject.created_at).all()
    achievements = db.query(ProfileAchievement).filter(
        ProfileAchievement.user_id == user_id
    ).order_by(ProfileAchievement.created_at).all()
    if not projects and not achievements:
        return ""

    lines = [
        "PROFILE POOL. Everything below was written or approved by the candidate and is true.",
        "It is NOT part of the current resume. Draw on an item ONLY when it makes the resume a",
        "stronger match for this job; using none of it is a valid choice, and never include it",
        "all by default. The page budget still applies: to make room for a pool project you may",
        "drop a less relevant project from the resume. Keep pool bullets factually as written",
        "(you may tighten wording and surface job keywords they genuinely support).",
    ]
    if projects:
        lines += ["", "POOL PROJECTS:"]
        for i, p in enumerate(projects, 1):
            tech = f" | Tech: {', '.join(p.tech_stack)}" if p.tech_stack else ""
            lines.append(f"{i}. {p.name}{': ' + p.summary if p.summary else ''}{tech}")
            lines += [f"   - {b}" for b in (p.bullets or [])]
    if achievements:
        lines += ["", "POOL ACHIEVEMENTS AND ACTIVITIES:"]
        lines += [f"- [{a.category}] {a.text}" for a in achievements]
    return "\n".join(lines)


# ---------------------------------------------------------------- helpers

def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Not found.")


def _own_project(db: Session, user: User, project_id: str) -> ProfileProject:
    p = db.query(ProfileProject).filter(
        ProfileProject.id == _uuid(project_id), ProfileProject.user_id == user.id
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


def _require_verified(user: User) -> None:
    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Please verify your email address first. Check your inbox for the verification link.")


def _clean_bullets(bullets: List[str], limit: int = 3) -> List[str]:
    out = []
    for b in bullets or []:
        text = re.sub(r"\s+", " ", str(b)).strip().lstrip("-•* ").strip()
        if text:
            out.append(text[:400])
    return out[:limit]


def _server_llm():
    llm = build_server_llm()
    if llm is None:
        raise HTTPException(status_code=500, detail="Server AI is temporarily unavailable. Please try again later.")
    return llm


def _github_http(e: gh.GitHubError, user: User, db: Session) -> HTTPException:
    """409 (never 401: the dashboard treats 401 as an expired Sculpt session)."""
    if e.reconnect:
        user.github_token_enc = None
        db.commit()
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=400, detail=str(e))


# Users with a Chisel generation running in this worker (a double-click must not take two slots)
_chisel_in_flight: set = set()


# ================================================================ profile

@router.get("/profile")
def get_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything the onboarding flow and the dashboard's Profile section show.
    Three queries in all (the user, their projects, their achievements): the allowance counts
    come from the projects already loaded, since every round trip to the database counts."""
    projects = db.query(ProfileProject).filter(
        ProfileProject.user_id == current_user.id).order_by(ProfileProject.created_at).all()
    achievements = db.query(ProfileAchievement).filter(
        ProfileAchievement.user_id == current_user.id).order_by(ProfileAchievement.created_at).all()
    return {
        **onboarding_state(current_user),
        "email_verified": bool(current_user.email_verified),
        "resume": {
            "has_resume": bool(current_user.resume_yaml),
            "filename": current_user.resume_filename,
            "uploaded_at": current_user.resume_uploaded_at.isoformat() if current_user.resume_uploaded_at else None,
        },
        "github": {
            "available": gh.is_configured(),
            "connected": bool(current_user.github_token_enc and current_user.github_username),
            "username": current_user.github_username,
            "private_access": bool(current_user.github_private_access),
        },
        "chisel": chisel_status(current_user, db, saved=sum(p.source == "github" for p in projects)),
        "manual": manual_status(current_user, db, saved=sum(p.source == "manual" for p in projects)),
        "projects": [project_out(p) for p in projects],
        "achievements": [achievement_out(a) for a in achievements],
        "limits": {
            "achievements": MAX_ACHIEVEMENTS,
            "categories": list(ACHIEVEMENT_CATEGORIES),
        },
    }


class OnboardingStepRequest(BaseModel):
    step: str

    @field_validator("step")
    @classmethod
    def known_step(cls, v):
        if v not in ONBOARDING_STEPS:
            raise ValueError("unknown step")
        return v


@router.post("/onboarding/step")
def set_onboarding_step(
    body: OnboardingStepRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move onboarding forward. Progress only ever advances, so leaving and coming back
    lands on the furthest step reached. The resume step can't be passed without a resume."""
    if isinstance(current_user.onboarding_completed_at, datetime):
        return onboarding_state(current_user)
    if body.step != "resume" and not current_user.resume_yaml:
        raise HTTPException(status_code=400, detail="Upload your resume first.")

    current = current_user.onboarding_step if current_user.onboarding_step in ONBOARDING_STEPS else "resume"
    if ONBOARDING_STEPS.index(body.step) > ONBOARDING_STEPS.index(current):
        current_user.onboarding_step = body.step
        if body.step == "done":
            current_user.onboarding_completed_at = _now()
        db.commit()
    return onboarding_state(current_user)


# ================================================================ GitHub connection

@router.get("/auth/github/start")
def github_start(
    private: bool = Query(False, description="Also ask for access to private repositories"),
    current_user: User = Depends(get_current_user),
):
    """The GitHub URL to send the browser to. The signed state ties the callback to this user."""
    if not gh.is_configured():
        raise HTTPException(status_code=503, detail="GitHub connection isn't configured on this server.")
    state = create_access_token(
        {"sub": str(current_user.id), "purpose": "github_oauth", "private": bool(private)},
        expires_delta=timedelta(minutes=10),
    )
    return {"url": gh.authorize_url(state, private)}


@router.get("/auth/github/callback")
async def github_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """GitHub sends the browser here. Link the account, then go back to the dashboard."""
    def back(result: str) -> RedirectResponse:
        return RedirectResponse(f"{dashboard_url()}/?github={result}", status_code=302)

    if error or not code or not state:
        return back("cancelled")
    try:
        claims = decode_token(state)
    except HTTPException:
        return back("expired")
    if claims.get("purpose") != "github_oauth" or not claims.get("sub"):
        return back("expired")

    try:
        token, scopes = await gh.exchange_code(code)
        profile = await gh.GitHub(token).user()
    except gh.GitHubError as e:
        logger.warning(f"[GITHUB] connect failed: {e}")
        return back("failed")

    def link() -> str:
        user = db.query(User).filter(User.id == _uuid(claims["sub"])).first()
        if not user:
            return "expired"
        github_id = str(profile.get("id"))
        other = db.query(User).filter(User.github_id == github_id, User.id != user.id).first()
        if other:
            return "in_use"  # one GitHub account backs one Sculpt account
        user.github_id = github_id
        user.github_username = profile.get("login")
        user.github_token_enc = gh.encrypt_token(token)
        user.github_private_access = "repo" in scopes
        db.commit()
        logger.info(f"[GITHUB] {user.email} connected as {user.github_username} (private={user.github_private_access})")
        return "connected"

    return back(await run_in_threadpool(link))


@router.delete("/auth/github")
def github_disconnect(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Forget the GitHub token. Projects already written stay in the profile."""
    current_user.github_token_enc = None
    current_user.github_private_access = False
    db.commit()
    return {"connected": False}


@router.get("/github/repos")
async def github_repos(
    page: int = Query(1, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = gh.decrypt_token(current_user.github_token_enc)
    if not token:
        raise HTTPException(status_code=409, detail="Connect GitHub first.")
    try:
        repos = await gh.GitHub(token).repos(page=page)
    except gh.GitHubError as e:
        raise await run_in_threadpool(_github_http, e, current_user, db)

    def added() -> set:
        rows = db.query(ProfileProject.repo_full_name).filter(
            ProfileProject.user_id == current_user.id, ProfileProject.source == "github").all()
        return {(r[0] or "").lower() for r in rows}

    taken = await run_in_threadpool(added)
    for r in repos:
        r["added"] = r["full_name"].lower() in taken
    return {"repos": repos, "page": page, "has_more": len(repos) == 30}


# ================================================================ Chisel

class ChiselCreateRequest(BaseModel):
    repo_full_name: str = Field(..., max_length=210)
    role: str = Field(..., min_length=3, max_length=500, description="The user's part in the project, in their words")
    facts: Optional[str] = Field(None, max_length=800, description="Real numbers or outcomes, if any")

    @field_validator("repo_full_name")
    @classmethod
    def valid_repo(cls, v):
        v = v.strip()
        if not _REPO_RE.match(v):
            raise ValueError("expected owner/repository")
        return v


@router.post("/chisel/projects", dependencies=[Depends(rate_limit("chisel", 10, 3600))])
async def chisel_create(
    body: ChiselCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read a repository the user contributed to and write three resume bullets for it."""
    _require_verified(current_user)
    token = gh.decrypt_token(current_user.github_token_enc)
    if not token or not current_user.github_username:
        raise HTTPException(status_code=409, detail="Connect GitHub first.")

    key = str(current_user.id)
    if key in _chisel_in_flight:
        raise HTTPException(status_code=409, detail="Chisel is already working on a project for you. Wait for it to finish.")
    _chisel_in_flight.add(key)
    try:
        def precheck():
            status = chisel_status(current_user, db)
            if not status["can_add"]:
                raise HTTPException(status_code=403, detail=status["reason"])
            dup = db.query(ProfileProject).filter(
                ProfileProject.user_id == current_user.id,
                ProfileProject.repo_full_name.ilike(body.repo_full_name),
            ).first()
            if dup:
                raise HTTPException(status_code=409, detail="That repository is already in your profile.")

        await run_in_threadpool(precheck)

        try:
            digest = await gh.GitHub(token).repo_digest(body.repo_full_name, current_user.github_username)
        except gh.GitHubError as e:
            raise await run_in_threadpool(_github_http, e, current_user, db)

        llm = _server_llm()
        try:
            result = await build_chisel_chain(llm).ainvoke({
                "role": body.role.strip(),
                "facts": (body.facts or "").strip() or "(none given)",
                "context": digest["context"],
            })
        except Exception as e:
            logger.error(f"[CHISEL] generation failed for {current_user.id}: {e}")
            raise HTTPException(status_code=502, detail="Chisel couldn't write bullets for this project. Nothing was used up; please try again.")

        bullets = _clean_bullets(result.bullets)
        if len(bullets) < 3:
            raise HTTPException(status_code=502, detail="Chisel couldn't write three bullets for this project. Nothing was used up; please try again.")

        def save() -> dict:
            project = ProfileProject(
                id=uuid.uuid4(),
                user_id=current_user.id,
                source="github",
                name=(result.project_name or digest["name"]).strip()[:120],
                summary=(result.summary or "").strip()[:300],
                tech_stack=[str(t).strip()[:40] for t in (result.tech_stack or []) if str(t).strip()][:8],
                bullets=bullets,
                status="draft",
                repo_full_name=body.repo_full_name,
                repo_url=digest["url"],
                repo_private=digest["private"],
                role=body.role.strip(),
                facts=(body.facts or "").strip() or None,
                repo_context=digest["context"],
                ai_edits_used=0,
            )
            db.add(project)
            # The add is counted in the same commit as the saved project
            count_chisel_add(current_user)
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            return {"project": project_out(project), "chisel": chisel_status(current_user, db)}

        out = await run_in_threadpool(save)
        logger.info(f"[CHISEL] {current_user.email} generated bullets for {body.repo_full_name}")
        return out
    finally:
        _chisel_in_flight.discard(key)


class ChiselReviseRequest(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=500)


@router.post("/chisel/projects/{project_id}/revise", dependencies=[Depends(rate_limit("chisel_revise", 30, 3600))])
async def chisel_revise(
    project_id: str,
    body: ChiselReviseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rework a Chisel project's bullets on the user's instruction (three rounds per project)."""
    _require_verified(current_user)
    project = await run_in_threadpool(_own_project, db, current_user, project_id)
    if project.source != "github":
        raise HTTPException(status_code=400, detail="Only Chisel projects can be revised by AI. Edit this one directly.")
    if (project.ai_edits_used or 0) >= CHISEL_AI_EDITS:
        raise HTTPException(
            status_code=409,
            detail=f"You've used all {CHISEL_AI_EDITS} AI edits for this project. You can still edit the bullets yourself.",
        )

    llm = _server_llm()
    try:
        result = await build_chisel_revise_chain(llm).ainvoke({
            "bullets": "\n".join(f"- {b}" for b in (project.bullets or [])),
            "instruction": body.instruction.strip(),
            "role": project.role or "(not given)",
            "facts": project.facts or "(none given)",
            "context": project.repo_context or "(repository digest unavailable)",
        })
    except Exception as e:
        logger.error(f"[CHISEL] revise failed for {current_user.id}: {e}")
        raise HTTPException(status_code=502, detail="Chisel couldn't revise the bullets. This attempt wasn't counted; please try again.")

    bullets = _clean_bullets(result.bullets)
    if len(bullets) < 3:
        raise HTTPException(status_code=502, detail="Chisel couldn't revise the bullets. This attempt wasn't counted; please try again.")

    def save() -> dict:
        project.bullets = bullets
        project.ai_edits_used = (project.ai_edits_used or 0) + 1
        project.status = "draft"  # changed bullets need the user's approval again
        db.commit()
        return project_out(project)

    return {"project": await run_in_threadpool(save)}


# ================================================================ projects (edit, add by hand, remove)

class ProjectPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    summary: Optional[str] = Field(None, max_length=300)
    tech_stack: Optional[List[str]] = None
    bullets: Optional[List[str]] = None
    status: Optional[str] = None

    @field_validator("status")
    @classmethod
    def known_status(cls, v):
        if v is not None and v not in ("draft", "approved"):
            raise ValueError("status must be draft or approved")
        return v


@router.patch("/profile/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edits the user types themselves (unlimited) and approval."""
    project = _own_project(db, current_user, project_id)
    if body.name is not None:
        project.name = body.name.strip()
    if body.summary is not None:
        project.summary = body.summary.strip()
    if body.tech_stack is not None:
        project.tech_stack = [str(t).strip()[:40] for t in body.tech_stack if str(t).strip()][:8]
    if body.bullets is not None:
        bullets = _clean_bullets(body.bullets)
        if not bullets:
            raise HTTPException(status_code=400, detail="A project needs at least one bullet.")
        project.bullets = bullets
    if body.status is not None:
        project.status = body.status
    db.commit()
    return {"project": project_out(project)}


class ManualProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    summary: Optional[str] = Field(None, max_length=300)
    tech_stack: Optional[List[str]] = None
    bullets: List[str]


@router.post("/profile/projects")
def add_manual_project(
    body: ManualProjectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A project the user describes themselves (no repository, no Chisel allowance)."""
    status = manual_status(current_user, db)
    if not status["can_add"]:
        if status["limit"] is not None:
            raise HTTPException(
                status_code=409,
                detail="The free plan includes one project added by hand. Remove it to add a different one.",
            )
        raise HTTPException(status_code=409, detail=f"You can add up to {MANUAL_PROJECT_SAFETY_CAP} projects by hand.")
    bullets = _clean_bullets(body.bullets)
    if not bullets:
        raise HTTPException(status_code=400, detail="Describe the project in at least one bullet.")
    project = ProfileProject(
        id=uuid.uuid4(),
        user_id=current_user.id,
        source="manual",
        name=body.name.strip(),
        summary=(body.summary or "").strip() or None,
        tech_stack=[str(t).strip()[:40] for t in (body.tech_stack or []) if str(t).strip()][:8],
        bullets=bullets,
        status="approved",  # the user wrote it
    )
    db.add(project)
    db.commit()
    return {"project": project_out(project), "manual": manual_status(current_user, db)}


@router.delete("/profile/projects/{project_id}")
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove any project, any time. A Chisel project's slot is freed; the add it used is not given back."""
    project = _own_project(db, current_user, project_id)
    db.delete(project)
    db.commit()
    return {"deleted": True, "chisel": chisel_status(current_user, db), "manual": manual_status(current_user, db)}


# ================================================================ achievements

class RefineRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=600)
    kind: str = Field("achievement")

    @field_validator("kind")
    @classmethod
    def known_kind(cls, v):
        if v not in ("achievement", "project"):
            raise ValueError("kind must be achievement or project")
        return v


@router.post("/profile/refine", dependencies=[Depends(rate_limit("refine", 40, 3600))])
async def refine_text(body: RefineRequest, current_user: User = Depends(get_current_user)):
    """Polish wording without adding facts. Nothing is saved: the user decides whether to keep it."""
    _require_verified(current_user)
    llm = _server_llm()
    original = body.text.strip()
    try:
        result = await build_refine_chain(llm).ainvoke({"kind": body.kind, "text": original})
    except Exception as e:
        logger.error(f"[REFINE] failed for {current_user.id}: {e}")
        raise HTTPException(status_code=502, detail="Couldn't polish that just now. Your text is unchanged; you can save it as it is.")
    refined = (re.sub(r"\s+", " ", (result.refined or "")).strip() or original)[:600]
    # "minor": only capitalisation, spacing or punctuation differs, so there is nothing to choose between
    bare = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    return {"refined": refined, "changed": refined != original, "minor": bare(refined) == bare(original)}


class AchievementRequest(BaseModel):
    category: str = Field("other")
    text: str = Field(..., min_length=3, max_length=400)
    original_text: Optional[str] = Field(None, max_length=600)

    @field_validator("category")
    @classmethod
    def known_category(cls, v):
        return v if v in ACHIEVEMENT_CATEGORIES else "other"


@router.post("/profile/achievements")
def add_achievement(
    body: AchievementRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = db.query(ProfileAchievement).filter(ProfileAchievement.user_id == current_user.id).count()
    if count >= MAX_ACHIEVEMENTS:
        raise HTTPException(status_code=409, detail=f"You can add up to {MAX_ACHIEVEMENTS} achievements.")
    row = ProfileAchievement(
        id=uuid.uuid4(),
        user_id=current_user.id,
        category=body.category,
        text=body.text.strip(),
        original_text=(body.original_text or "").strip() or None,
    )
    db.add(row)
    db.commit()
    return {"achievement": achievement_out(row)}


def _own_achievement(db: Session, user: User, achievement_id: str) -> ProfileAchievement:
    row = db.query(ProfileAchievement).filter(
        ProfileAchievement.id == _uuid(achievement_id), ProfileAchievement.user_id == user.id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Achievement not found.")
    return row


@router.patch("/profile/achievements/{achievement_id}")
def update_achievement(
    achievement_id: str,
    body: AchievementRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _own_achievement(db, current_user, achievement_id)
    row.category = body.category
    row.text = body.text.strip()
    db.commit()
    return {"achievement": achievement_out(row)}


@router.delete("/profile/achievements/{achievement_id}")
def delete_achievement(
    achievement_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.delete(_own_achievement(db, current_user, achievement_id))
    db.commit()
    return {"deleted": True}
