from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Date, UniqueConstraint, ForeignKey
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

    # Resume stored directly on the user (one active resume at a time)
    resume_yaml         = Column(Text, nullable=True)
    resume_filename     = Column(String, nullable=True)
    resume_uploaded_at  = Column(DateTime(timezone=True), nullable=True)

    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    optimized_resumes   = relationship("OptimizedResume", back_populates="user", cascade="all, delete-orphan")
    generation_usage    = relationship("GenerationUsage",  back_populates="user", cascade="all, delete-orphan")


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
    keywords_added      = Column(JSONB, nullable=True)                          # List[str]
    improvements_made   = Column(JSONB, nullable=True)                          # List[str]
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
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    user        = relationship("User", back_populates="generation_usage")

    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_generation_usage_user_week"),
    )