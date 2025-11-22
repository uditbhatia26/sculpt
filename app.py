from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from schema.schema import JDRequest, parsedJobDescription, UserLogin, UserSignup
from dotenv import load_dotenv
from config.file_helpers import load_data, save_data
from io import BytesIO
import yaml
from datetime import datetime
import os
from PyPDF2 import PdfReader
from models.chains import jd_parser_chain, groq_llm, res2yaml_chain
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()


FILE_PATH = 'data.json'
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
        "model_loaded": groq_llm is not None,

    }


# ---------------------------- Sample code for signup and login ---------------------------- #
@app.post('/auth/signup')
def create_user(credentials: UserSignup):
    data = load_data(FILE_PATH)
    
    if credentials.id in data:
        raise HTTPException(
            status_code=409,
            detail="User already exists"
        )

    data[credentials.id] = credentials.model_dump(exclude=['id'])
    save_data(data)
    return JSONResponse(
        status_code=200, 
        content="Successfully Signed Up"
    )


@app.post('/auth/login')
def authenticate_user(credentials: UserLogin):
    data = load_data(FILE_PATH)

    for i in data:
        if data[i]['email'] == credentials.email and data[i]['password'] == credentials.password:
            return JSONResponse(
                status_code=200,
                content="Logged In successfully"
            )
    
    raise HTTPException(
        status_code = 401,
        detail="Credentials are incorrect"
    )

# ---------------------------- Sample code for signup and login ---------------------------- #


# FIX 1: Handle the entered job description, currently not able to send it via a curl request
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
        logger.error(f"Failed to parse job description: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Failed to process job description with LLM",
                "message": str(e)
            }
        )
    

@app.post('/upload-resume')
async def upload_resume(user_id: str, file: UploadFile = File("Resume of the user")):
    if not user_id or not user_id.strip():
        raise HTTPException(
            status_code=400,
            detail="User ID cannot be empty"
        )

    data = load_data(FILE_PATH)

    debug_folder = "debug_resumes"
    os.makedirs(debug_folder, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    yaml_filename = os.path.join(debug_folder, f"resume_{user_id}_{timestamp}.yaml")

    if user_id not in data:
        raise HTTPException(
            status_code=404,
            detail="User does not exist"
      )
    
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed."
        )
    
    if (int(file.size) / 1048576) > 2:
        raise HTTPException(
            status_code=400,
            detail={
            "message": "Resume size is too large, Please upload a resume with size less than 2MB",
            "current_file_size": f"{(int(file.size) / 1048576):.2f}MB"
            }
        )
    
    try:
        pdf_bytes = await file.read()
        pdf_file = BytesIO(pdf_bytes)

        reader = PdfReader(pdf_file)
        resume_content = ""
        for page in reader.pages:
            resume_content += page.extract_text()
    
    except Exception as e:
        logger.error(f"Failed to read PDF for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail="Failed to read PDF file. The file may be corrupted or password protected"
        )
    
    if not resume_content or not resume_content.strip():
        raise HTTPException(
            status_code=400,
            detail="No text found in PDF. Please ensure the Resume is not empty"
        )
    
    response = await res2yaml_chain.ainvoke(
        input={
            "resume_content": resume_content
        }
    )

    resume_yaml = response.content

    if "```" in resume_yaml:
        resume_yaml = resume_yaml.split("```yaml")[-1] if "```yaml" in resume_yaml else resume_yaml.split("```")[-1]
        resume_yaml = resume_yaml.split("```")[0]
        resume_yaml = resume_yaml.strip()

    try:
        yaml_data = yaml.safe_load(resume_yaml)
        with open(yaml_filename, 'w', encoding='utf-8') as f:
            yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error for user {user_id}: {str(e)}")
        with open(yaml_filename, 'w', encoding='utf-8') as f:
            f.write(resume_yaml)
    
    try:
        data[user_id]['resume_yaml'] = resume_yaml
        save_data(data=data)
    
    except Exception as e:
        logger.error(f"Failed to save data for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save resume data. Please try again"
        )

    logger.info(f"Successfully processed resume for user {user_id}")

    return JSONResponse(
        status_code=201,
        content={
            "message": "Resume YAML added successfully",
            "filename": yaml_filename,
        }
    )