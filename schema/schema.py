from pydantic import BaseModel, Field, EmailStr
from typing import Annotated, List, Optional
from uuid import UUID


# ==================== AUTHENTICATION SCHEMAS ====================

class UserSignup(BaseModel):
    email:       Annotated[EmailStr, Field(..., description="Email of the user")]
    password:    Annotated[str,      Field(..., description="User's password", min_length=6)]
    full_name:   Annotated[Optional[str], Field(None, description="User's full name")]


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
    weekly_usage:     int           = Field(description="Generations used this week")
    weekly_limit:     int           = Field(description="Max generations allowed per week for their plan")


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
    created_at:       str


# ==================== JOB DESCRIPTION SCHEMAS ====================

class parsedJobDescription(BaseModel):
    skills:          Annotated[list[str], Field(..., description="Required Skills")]
    job_description: Annotated[str,       Field(..., description="Job Description")]
    job_title:       Annotated[str,       Field(..., description="Job Title")]


# ==================== ATS SCORING SCHEMAS ====================

class CalculateATS(BaseModel):
    job_desc: Annotated[str, Field(..., description="Job Description provided by the user")]


class ATS(BaseModel):
    ats_score: Annotated[int, Field(..., description="ATS score of the resume according to the job description", gt=0, le=100)]
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
    job_desc: Annotated[str, Field(..., description="Job Description provided by the user")]


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