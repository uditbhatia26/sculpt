from pydantic import BaseModel, Field, EmailStr
from typing import Annotated, List

class parsedJobDescription(BaseModel):
    skills : Annotated[list[str], Field(..., description="Required Skills")]
    job_description : Annotated[str, Field(..., description="Job Description")]
    job_title : Annotated[str, Field(..., description="Job Title")]


class UserSignup(BaseModel):
    id: Annotated[str, Field(..., description="ID of the user")]
    name: Annotated[str, Field(..., description="User's name", min_length=3)]
    email: Annotated[EmailStr, Field(..., description="Email of the user")]
    password: Annotated[str, Field(..., description="User's password", min_length=6)]


class UserLogin(BaseModel):
    email: Annotated[EmailStr, Field(..., description="Email of the user")]
    password: Annotated[str, Field(..., description="Password of the user")]


class CalculateATS(BaseModel):
    user_id: Annotated[str, Field(..., description="ID of the user")]
    job_desc: Annotated[str, Field(..., description="Job Description provided by the user")]


class ATS(BaseModel):
    ats_score: Annotated[int, Field(..., description="ATS score of the resume according to the job description", gt=0, le=100)]
    reason: Annotated[str, Field(description="Reason for the score")]


class KeywordMatch(BaseModel):
    """Detailed keyword matching analysis"""
    matched_keywords: List[str] = Field(description="Keywords from JD found in resume")
    missing_critical_keywords: List[str] = Field(description="Must-have keywords missing")
    missing_important_keywords: List[str] = Field(description="Nice-to-have keywords missing")
    keyword_density_score: float = Field(description="0-100 score for keyword coverage")

class SkillsAnalysis(BaseModel):
    """Technical and soft skills alignment"""
    matched_technical_skills: List[str]
    missing_technical_skills: List[str]
    matched_soft_skills: List[str]
    missing_soft_skills: List[str]
    skills_alignment_score: float = Field(description="0-100")

class ExperienceAlignment(BaseModel):
    """Experience relevance analysis"""
    relevant_years: float = Field(description="Years of relevant experience")
    required_years: float = Field(description="Required years from JD")
    role_alignment: str = Field(description="How well roles match (High/Medium/Low)")
    experience_score: float = Field(description="0-100")

class FormattingScore(BaseModel):
    """ATS-friendly formatting check"""
    has_clear_sections: bool
    uses_standard_headers: bool
    bullet_point_quality: str = Field(description="Excellent/Good/Poor")
    readability_score: float = Field(description="0-100")

class DetailedATS(BaseModel):
    """Comprehensive ATS analysis with breakdown"""
    overall_score: float = Field(description="Final ATS score 0-100", ge=0, le=100)
    
    # Component scores
    keyword_analysis: KeywordMatch
    skills_analysis: SkillsAnalysis
    experience_alignment: ExperienceAlignment
    formatting_score: FormattingScore
    
    # Score breakdown
    keyword_weight_score: float = Field(description="Weighted keyword score (40% of total)")
    skills_weight_score: float = Field(description="Weighted skills score (30% of total)")
    experience_weight_score: float = Field(description="Weighted experience score (20% of total)")
    formatting_weight_score: float = Field(description="Weighted formatting score (10% of total)")
    
    # Actionable feedback
    strengths: List[str] = Field(description="Top 3-5 strengths")
    critical_improvements: List[str] = Field(description="Must-fix issues")
    recommended_improvements: List[str] = Field(description="Nice-to-have improvements")
    
    # Match level
    match_level: str = Field(description="Excellent/Good/Fair/Poor")
    likelihood_to_pass_ats: str = Field(description="High/Medium/Low")

# ==================== OPTIMIZATION REQUEST SCHEMAS ====================

class OptimizeResumeRequest(BaseModel):
    user_id: Annotated[str, Field(..., description="ID of the user")]
    job_desc: Annotated[str, Field(..., description="Job Description provided by the user")]

class OptimizedResumeResponse(BaseModel):
    original_score: float = Field(description="Original ATS score before optimization")
    optimized_score: float = Field(description="Predicted ATS score after optimization")
    optimized_resume_yaml: str = Field(description="The optimized resume in YAML format")
    improvements_made: List[str] = Field(description="List of key improvements made")
    keywords_added: List[str] = Field(description="Keywords integrated from job description")