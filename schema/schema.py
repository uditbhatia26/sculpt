from pydantic import BaseModel, Field, EmailStr
from typing import Annotated, List, Optional
from uuid import UUID


# ==================== AI PROVIDER CONFIG (BYOK) ====================

class AIProviderConfig(BaseModel):
    """Bring-Your-Own-Key AI provider configuration.
    Sent by the extension on every LLM-backed request.
    The api_key is never stored in the database.
    """
    provider: Annotated[str,  Field(...,  description="Provider name: openai | anthropic | google | groq | openrouter")]
    api_key:  Annotated[str,  Field(...,  description="The user's API key for this provider", min_length=8)]
    model:    Annotated[Optional[str], Field(None, description="Model name override. Falls back to provider default if omitted.")]


# ==================== AUTHENTICATION SCHEMAS ====================

class UserSignup(BaseModel):
    email:       Annotated[EmailStr, Field(..., description="Email of the user")]
    password:    Annotated[str,      Field(..., description="User's password", min_length=6, max_length=128)]
    full_name:   Annotated[Optional[str], Field(None, description="User's full name", max_length=120)]


class UserLogin(BaseModel):
    email:    Annotated[EmailStr, Field(..., description="Email of the user")]
    password: Annotated[str,      Field(..., description="Password of the user")]


class AuthResponse(BaseModel):
    access_token:     str           = Field(description="JWT access token")
    token_type:       str           = Field(default="bearer", description="Token type")
    user_id:          str           = Field(description="User's UUID")
    email:            str           = Field(description="User's email")
    full_name:        Optional[str] = Field(description="User's full name")
    plan:             str           = Field(description="User plan: free or pro")
    has_resume:       bool          = Field(description="Whether the user has uploaded a resume")
    resume_filename:  Optional[str] = Field(None, description="Filename of the user's current resume, if any")
    email_verified:   bool          = Field(default=False, description="Whether the user's email has been verified")
    weekly_usage:     int           = Field(description="Generations used this week")
    weekly_limit:     int           = Field(description="Max generations allowed per week for their plan")
    daily_usage:      int           = Field(default=0, description="Generations made today")
    monthly_usage:    int           = Field(default=0, description="Generations made this calendar month")



class UserProfile(BaseModel):
    user_id:          str
    email:            str
    full_name:        Optional[str]
    plan:             str
    has_resume:       bool
    resume_filename:  Optional[str]
    resume_uploaded_at: Optional[str]
    weekly_usage:     int
    weekly_limit:     int
    daily_usage:      int = 0
    monthly_usage:    int = 0
    created_at:       str


# ==================== JOB DESCRIPTION SCHEMAS ====================

class parsedJobDescription(BaseModel):
    skills:          Annotated[list[str], Field(..., description="Required Skills")]
    job_description: Annotated[str,       Field(..., description="Job Description")]
    job_title:       Annotated[str,       Field(..., description="Job Title")]


# ==================== ATS SCORING SCHEMAS ====================

class CalculateATS(BaseModel):
    job_desc:     Annotated[str, Field(..., description="Job Description provided by the user", min_length=50, max_length=20_000)]
    jd_cache_id:  Optional[str] = Field(None, description="ID of a previously parsed and cached JD. If provided the LLM parse step is skipped.")
    ai_config:    AIProviderConfig = Field(..., description="BYOK AI provider configuration")


class ATS(BaseModel):
    ats_score: Annotated[int, Field(..., description="ATS score of the resume according to the job description", ge=0, le=100)]
    reason:    Annotated[str, Field(description="Reason for the score")]


class KeywordMatch(BaseModel):
    """Detailed keyword matching analysis"""
    matched_keywords:           List[str] = Field(description="Keywords from JD found in resume")
    missing_critical_keywords:  List[str] = Field(description="Must-have keywords missing")
    missing_important_keywords: List[str] = Field(description="Nice-to-have keywords missing")
    keyword_density_score:      float     = Field(description="0-100 score for keyword coverage")


class SkillsAnalysis(BaseModel):
    """Technical and soft skills alignment"""
    matched_technical_skills: List[str]
    missing_technical_skills: List[str]
    matched_soft_skills:      List[str]
    missing_soft_skills:      List[str]
    skills_alignment_score:   float = Field(description="0-100")


class ExperienceAlignment(BaseModel):
    """Experience relevance analysis"""
    relevant_years:   float = Field(description="Years of relevant experience")
    required_years:   float = Field(description="Required years from JD")
    role_alignment:   str   = Field(description="How well roles match (High/Medium/Low)")
    experience_score: float = Field(description="0-100")


class FormattingScore(BaseModel):
    """ATS-friendly formatting check"""
    has_clear_sections:    bool
    uses_standard_headers: bool
    bullet_point_quality:  str   = Field(description="Excellent/Good/Poor")
    readability_score:     float = Field(description="0-100")


class DetailedATS(BaseModel):
    """Comprehensive ATS analysis with breakdown"""
    overall_score: float = Field(description="Final ATS score 0-100", ge=0, le=100)

    keyword_analysis:     KeywordMatch
    skills_analysis:      SkillsAnalysis
    experience_alignment: ExperienceAlignment
    formatting_score:     FormattingScore

    keyword_weight_score:    float = Field(description="Weighted keyword score (40% of total)")
    skills_weight_score:     float = Field(description="Weighted skills score (30% of total)")
    experience_weight_score: float = Field(description="Weighted experience score (20% of total)")
    formatting_weight_score: float = Field(description="Weighted formatting score (10% of total)")

    strengths:                List[str] = Field(description="Top 3-5 strengths")
    critical_improvements:    List[str] = Field(description="Must-fix issues")
    recommended_improvements: List[str] = Field(description="Nice-to-have improvements")

    match_level:           str = Field(description="Excellent/Good/Fair/Poor")
    likelihood_to_pass_ats: str = Field(description="High/Medium/Low")


# ==================== OPTIMIZATION REQUEST SCHEMAS ====================

class OptimizeResumeRequest(BaseModel):
    job_desc:           Annotated[str, Field(..., description="Job Description provided by the user", min_length=50, max_length=20_000)]
    jd_cache_id:        Optional[str]   = Field(None,  description="ID of a previously parsed and cached JD. If provided, the LLM JD parse step is skipped.")
    original_ats_score: Optional[float] = Field(None,  description="ATS score already computed by /calculate-ats-detailed. If provided, the original ATS LLM call is skipped.", ge=0, le=100)
    ai_config:          AIProviderConfig = Field(...,  description="BYOK AI provider configuration")


# ==================== KEY VALIDATION SCHEMA ====================

class ValidateKeyRequest(BaseModel):
    """Used by POST /validate-key to test a user's API key."""
    provider: Annotated[str, Field(..., description="Provider name")]
    api_key:  Annotated[str, Field(..., description="The user's API key", min_length=8)]
    model:    Annotated[Optional[str], Field(None, description="Optional model override")]


class OptimizedResumeResponse(BaseModel):
    original_score:        float     = Field(description="Original ATS score before optimization")
    optimized_score:       float     = Field(description="Predicted ATS score after optimization")
    score_improvement:     float     = Field(description="Score delta")
    optimized_resume_yaml: str       = Field(description="The optimized resume in YAML format")
    improvements_made:     List[str] = Field(description="List of key improvements made")
    keywords_added:        List[str] = Field(description="Keywords integrated from job description")
    match_level:           str       = Field(description="Excellent/Good/Fair/Poor")
    weekly_usage:          int       = Field(description="Generations used this week after this call")
    weekly_limit:          int       = Field(description="Total allowed generations per week")


# ==================== PDF GENERATION SCHEMAS ====================

class GeneratePDFRequest(BaseModel):
    resume_yaml: Optional[str] = Field(
        None,
        description="YAML string of the resume to render. If omitted, the user's stored base resume is used.",
        max_length=100_000,
    )