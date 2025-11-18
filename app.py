from fastapi import FastAPI, Query, HTTPException, Path, Body
from fastapi.responses import JSONResponse
from schema.schema import JDRequest, parsedJobDescription
from dotenv import load_dotenv
from models.job_desc import jd_parser_chain
import os
load_dotenv()


app = FastAPI()
    

@app.get('/')
def home():
    return {
        "response": "Sculpt: Resume Optimization Platform"
    }


@app.get("/health")
def health():
    return {
        "status": "OK",
    }


@app.post("/job_description", response_model=parsedJobDescription)
async def parse_jd(request: JDRequest):
    try:
        response = await jd_parser_chain.ainvoke(
            input = {
                'job_description': request.job_description
            },
        )
        return response

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Failed to process job description with LLM",
                "message": str(e)
            }
        )