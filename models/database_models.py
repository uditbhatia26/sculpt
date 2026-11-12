from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Date, UniqueConstraint, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from config.database import Base
import uuid


class User(Base):
    __tablename__ = "users"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email               = Column(String, unique=True, index=True, nullable=False)
    password_hash       = Column(String, nullable=False)
    full_name           = Column(String, nullable=True)
    plan                = Column(String, nullable=False, default="free")        # 'free' | 'pro'

    # Email verification
    email_verified              = Column(Boolean, nullable=False, default=False, server_default="false")
    email_verification_token    = Column(String, nullable=True, index=True)      # One-time token sent in email
    email_verification_sent_at  = Column(DateTime(timezone=True), nullable=True) # When last email was sent

    # Password reset (stores a SHA-256 of the emailed token, never the token itself)
    password_reset_token_hash   = Column(String, nullable=True, index=True)
    password_reset_expires_at   = Column(DateTime(timezone=True), nullable=True)

    # Entitlements & Admin (Pillar 1)
    is_byok_allowed             = Column(Boolean, nullable=False, default=False, server_default="false")
    is_admin                    = Column(Boolean, nullable=False, default=False, server_default="false")

    # Resume stored directly on the user (one active resume at a time)
    resume_yaml         = Column(Text, nullable=True)
    resume_latex        = Column(Text, nullable=True)
    selected_template   = Column(String, default="jakes_resume")
    resume_filename     = Column(String, nullable=True)
    resume_uploaded_at  = Column(DateTime(timezone=True), nullable=True)

    # Onboarding on the website: resume -> projects -> achievements -> done
    onboarding_step             = Column(String, nullable=False, default="resume", server_default="resume")
    onboarding_completed_at     = Column(DateTime(timezone=True), nullable=True)
    # Accounts that existed before onboarding was introduced keep using the extension
    onboarding_grandfathered    = Column(Boolean, nullable=False, default=False, server_default="false")

    # GitHub connection (for Chisel). The token is stored encrypted, never in plain text.
    github_id                   = Column(String, nullable=True, unique=True)
    github_username             = Column(String, nullable=True)
    github_token_enc            = Column(Text, nullable=True)
    github_private_access       = Column(Boolean, nullable=False, default=False, server_default="false")
    # Chisel generations ever made (a statistic; limits use the allowance period below)
    chisel_generations_used     = Column(Integer, nullable=False, default=0, server_default="0")
    # Chisel allowance: a period starts with the first add and lasts CHISEL_PERIOD_DAYS.
    # Adds made in it are counted here; removing a project does not give one back.
    chisel_period_start         = Column(DateTime(timezone=True), nullable=True)
    chisel_period_adds          = Column(Integer, nullable=False, default=0, server_default="0")

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    optimized_resumes   = relationship("OptimizedResume", back_populates="user", cascade="all, delete-orphan")
    generation_usage    = relationship("GenerationUsage",  back_populates="user", cascade="all, delete-orphan")
    profile_projects    = relationship("ProfileProject", back_populates="user", cascade="all, delete-orphan")
    profile_achievements = relationship("ProfileAchievement", back_populates="user", cascade="all, delete-orphan")



class OptimizedResume(Base):
    __tablename__ = "optimized_resumes"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    job_description     = Column(Text, nullable=False)
    job_title           = Column(String, nullable=True)                         # Parsed from JD
    original_ats_score  = Column(Float, nullable=False)
    optimized_ats_score = Column(Float, nullable=False)
    score_improvement   = Column(Float, nullable=False)
    match_level         = Column(String, nullable=True)                         # Excellent/Good/Fair/Poor
    optimized_yaml      = Column(Text, nullable=False)
    resume_latex        = Column(Text, nullable=True)
    selected_template   = Column(String, default="jakes_resume")
    keywords_added      = Column(JSONB, nullable=True)                          # List[str]
    improvements_made   = Column(JSONB, nullable=True)                          # List[str]
    ai_provider         = Column(String, nullable=True)                         # BYOK: provider used (openai/anthropic/…)
    ai_model            = Column(String, nullable=True)                         # BYOK: model used (gpt-4o-mini/…)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    user                = relationship("User", back_populates="optimized_resumes")



class GenerationUsage(Base):
    """Tracks weekly resume generations per user for the paywall."""
    __tablename__ = "generation_usage"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    week_start  = Column(Date, nullable=False)   # Monday of the ISO week, e.g. 2026-02-23
    count       = Column(Integer, nullable=False, default=0)
    updated_at  = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    # Relationship
    user        = relationship("User", back_populates="generation_usage")

    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_generation_usage_user_week"),
    )


class ParsedJDCache(Base):
    """Caches parsed job descriptions keyed by a SHA-256 hash of the raw JD text.
    Prevents duplicate LLM calls when the same JD is used across Analyze + Generate.
    """
    __tablename__ = "parsed_jd_cache"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    jd_hash     = Column(String(64), unique=True, nullable=False, index=True)  # SHA-256 hex digest
    job_title   = Column(String, nullable=True)
    skills      = Column(JSONB, nullable=False)      # List[str]
    job_description = Column(Text, nullable=False)   # Cleaned/condensed JD text from LLM
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class ProfileProject(Base):
    """A project in the user's profile pool: written by Chisel from a GitHub repo, or typed in.
    The optimizer may draw on approved projects when one fits the job."""
    __tablename__ = "profile_projects"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source          = Column(String, nullable=False, default="manual")      # 'github' | 'manual'
    name            = Column(String, nullable=False)
    summary         = Column(Text, nullable=True)                           # one line: what it is
    tech_stack      = Column(JSONB, nullable=True)                          # ["FastAPI", "PostgreSQL"]
    bullets         = Column(JSONB, nullable=False, default=list)           # up to 3 resume bullets
    status          = Column(String, nullable=False, default="draft")       # 'draft' | 'approved'

    # GitHub (Chisel) projects only
    repo_full_name  = Column(String, nullable=True)                         # "owner/repo"
    repo_url        = Column(String, nullable=True)
    repo_private    = Column(Boolean, nullable=False, default=False, server_default="false")
    role            = Column(Text, nullable=True)                           # the user's own words about their part
    facts           = Column(Text, nullable=True)                           # real numbers/outcomes the user supplied
    repo_context    = Column(Text, nullable=True)                           # condensed repo digest, reused for edits
    ai_edits_used   = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until    = Column(DateTime(timezone=True), nullable=True)        # can't be removed before this

    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    user            = relationship("User", back_populates="profile_projects")


class ProfileAchievement(Base):
    """An achievement or activity the user added: hackathon result, leadership role, sport, event."""
    __tablename__ = "profile_achievements"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category        = Column(String, nullable=False, default="other")
    text            = Column(Text, nullable=False)                          # what goes on a resume
    original_text   = Column(Text, nullable=True)                           # what the user first typed
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    user            = relationship("User", back_populates="profile_achievements")
