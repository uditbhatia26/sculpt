from pydantic import BaseModel, Field, EmailStr
from typing import Annotated

class parsedJobDescription(BaseModel):
    skills : Annotated[list[str], Field(..., description="Required Skills")]
    job_description : Annotated[str, Field(..., description="Job Description")]
    job_title : Annotated[str, Field(..., description="Job Title")]

# Not being used
class JDRequest(BaseModel):
    job_description: Annotated[str, Field(..., description="Job Description provided by the user")]


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