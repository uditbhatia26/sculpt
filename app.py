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

@app.post('/upload-resume')
async def upload_resume(user_id: str, file: UploadFile = File("Resume of the user")):
    data = load_data(FILE_PATH)

    debug_folder = "debug_resumes"
    os.makedirs(debug_folder, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    yaml_filename = os.path.join(debug_folder, f"resume_{user_id}_{timestamp}.yaml")

    if user_id not in data:
        raise HTTPException(
            status_code=404,
            detail="User does not exists"
      )
    
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed."
        )
    
    pdf_bytes = await file.read()
    pdf_file = BytesIO(pdf_bytes)

    reader = PdfReader(pdf_file)
    resume_content = ""
    for page in reader.pages:
        resume_content += page.extract_text()


    response = res2yaml_chain.invoke(
        input={
            "resume_content": resume_content
        }
    )

    resume_yaml = response.content
    if "```" in resume_yaml:
        print("Still getting backticks")
        resume_yaml = resume_yaml.split("```yaml")[-1] if "```yaml" in resume_yaml else resume_yaml.split("```")[-1]
        resume_yaml = resume_yaml.split("```")[0]
        resume_yaml = resume_yaml.strip()

    try:
        yaml_data = yaml.safe_load(resume_yaml)
        with open(yaml_filename, 'w', encoding='utf-8') as f:
            yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    except yaml.YAMLError as e:
        print(f"YAML parsing error: {e}")
        with open(yaml_filename, 'w', encoding='utf-8') as f:
            f.write(resume_yaml)
    
    data[user_id]['resume_yaml'] = resume_yaml
    save_data(data=data)

    return JSONResponse(
        status_code=200,
        content={
            "message": "Resume YAML added successfully",
            "resume_yaml": resume_yaml,
            "resume_content": resume_content
        }
    )