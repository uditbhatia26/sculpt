from pydantic import BaseModel, Field
from typing import Annotated

class parsedJobDescription(BaseModel):
    skills : Annotated[list[str], Field(..., description="Required Skills")]
    job_description : Annotated[str, Field(..., description="Job Description")]
    job_title : Annotated[str, Field(..., description="Job Title")]


class JDRequest(BaseModel):
    job_description: Annotated[str, Field(..., description="Job Description provided by the user")]