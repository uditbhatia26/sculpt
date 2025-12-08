from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from schema.schema import UserLogin, UserSignup, CalculateATS
from dotenv import load_dotenv
from config.file_helpers import load_data, save_data
from io import BytesIO
from schema.schema import DetailedATS, OptimizeResumeRequest
from config.resume_functions import ats_detailed, optimize_resume
import yaml
from datetime import datetime
import os
from PyPDF2 import PdfReader
from models.chains import llm, res2yaml_chain
from config.resume_functions import parse_jd
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()   
    ]
)
logger = logging.getLogger("Sculpt")
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
        "model_loaded": llm is not None,

    }


# ---------------------------- Sample code for signup and login ---------------------------- #
@app.post('/auth/signup')
def create_user(credentials: UserSignup):
    """Creates a new user"""
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
    """Verify the entered credentials"""
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
async def upload_resume(user_id: str, file: UploadFile = File(..., description="Resume of the user")):
    """Function for uploading the user's resume in the database"""
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
            detail={
                "message": "Only PDF files are allowed.",
                "current_file_type": file.content_type
            }
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


# @app.post('/calculate-ats', response_model=ATS)
# async def calculate_ats(request: CalculateATS):
#     logger.info(f"Calculating ATS for user {request.user_id}")
#     if not request.user_id or not request.user_id.strip():
#         raise HTTPException(
#             status_code=400,
#             detail="User ID cannot be empty"
#         )
    
#     if not request.job_desc or not request.job_desc.strip():
#         raise HTTPException(
#             status_code=400,
#             detail="Job description cannot be empty"
#         )
    
#     parsed_jd = await parse_jd(job_description=request.job_desc)
    
#     data = load_data(FILE_PATH)

#     if request.user_id not in data:
#         raise HTTPException(
#             status_code=404,
#             detail="User not found"
#         )
    
#     try:
#         resume_yaml = data[request.user_id]['resume_yaml']

#     except KeyError as e:
#         logger.error(f"Resume not found for user {request.user_id}: {str(e)}")
#         raise HTTPException(
#             status_code=404, 
#             detail=f"Resume for the user is not available, {str(e)}"
#         )
    
#     try:
#         response = await ats(resume_yaml, parsed_jd)
#         logger.info(f"ATS calculated successfully for user {request.user_id}: {response.ats_score}")
#         return response
    
#     except Exception as e:
#         logger.error(f"Failed to calculate ATS for user {request.user_id}: {str(e)}")
#         raise HTTPException(
#             status_code=500,
#             detail=f"Unable to calculate ATS, {str(e)}"
#         )

    
@app.post('/calculate-ats-detailed', response_model=DetailedATS)
async def calculate_ats_detailed(request: CalculateATS):
    """Enhanced ATS calculation with detailed breakdown and actionable feedback"""
    logger.info(f"Calculating detailed ATS for user {request.user_id}")
    
    if not request.user_id or not request.user_id.strip():
        raise HTTPException(status_code=400, detail="User ID cannot be empty")
    
    if not request.job_desc or not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty")
    
    parsed_jd = await parse_jd(job_description=request.job_desc)
    data = load_data(FILE_PATH)
    
    if request.user_id not in data:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        resume_yaml = data[request.user_id]['resume_yaml']
    except KeyError as e:
        logger.error(f"Resume not found for user {request.user_id}: {str(e)}")
        raise HTTPException(
            status_code=404, 
            detail=f"Resume for the user is not available, {str(e)}"
        )
    
    try:
        response = await ats_detailed(resume_yaml, parsed_jd)
        logger.info(f"Detailed ATS calculated for user {request.user_id}: {response.overall_score}")
        return response
    
    except Exception as e:
        logger.error(f"Failed to calculate detailed ATS for user {request.user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to calculate detailed ATS, {str(e)}"
        )


@app.post('/optimize-resume')
async def optimize_resume_endpoint(request: OptimizeResumeRequest):
    """Optimize resume based on job description and calculate before/after scores"""
    logger.info(f"Optimizing resume for user {request.user_id}")
    
    if not request.user_id or not request.user_id.strip():
        raise HTTPException(status_code=400, detail="User ID cannot be empty")
    
    if not request.job_desc or not request.job_desc.strip():
        raise HTTPException(status_code=400, detail="Job description cannot be empty")
    
    # Parse job description
    parsed_jd = await parse_jd(job_description=request.job_desc)
    data = load_data(FILE_PATH)
    
    if request.user_id not in data:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        original_resume_yaml = data[request.user_id]['resume_yaml']
    except KeyError as e:
        logger.error(f"Resume not found for user {request.user_id}: {str(e)}")
        raise HTTPException(
            status_code=404, 
            detail=f"Resume for the user is not available, {str(e)}"
        )
    
    try:
        # Calculate original ATS score
        original_ats = await ats_detailed(original_resume_yaml, parsed_jd)
        logger.info(f"Original ATS score for user {request.user_id}: {original_ats.overall_score}")
        
        # Optimize resume
        optimized_yaml = await optimize_resume(
            resume_content=original_resume_yaml,
            job_description=parsed_jd,
            addons= "" # data[request.user_id]['addons']
        )
        
        # Calculate optimized ATS score
        optimized_ats = await ats_detailed(optimized_yaml, parsed_jd)
        logger.info(f"Optimized ATS score for user {request.user_id}: {optimized_ats.overall_score}")
        
        # Save optimized resume
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        optimized_folder = "optimized_resumes"
        os.makedirs(optimized_folder, exist_ok=True)
        optimized_filename = os.path.join(optimized_folder, f"optimized_{request.user_id}_{timestamp}.yaml")
        
        with open(optimized_filename, 'w', encoding='utf-8') as f:
            f.write(optimized_yaml)
        
        # Extract improvements made
        improvements_made = [
            f"Score improved from {original_ats.overall_score:.1f} to {optimized_ats.overall_score:.1f}",
            f"Added {len(optimized_ats.keyword_analysis.matched_keywords) - len(original_ats.keyword_analysis.matched_keywords)} more matching keywords",
        ]
        
        # Add specific improvements from the detailed analysis
        if len(original_ats.keyword_analysis.missing_critical_keywords) > len(optimized_ats.keyword_analysis.missing_critical_keywords):
            improvements_made.append("Integrated critical missing keywords")
        
        if optimized_ats.skills_analysis.skills_alignment_score > original_ats.skills_analysis.skills_alignment_score:
            improvements_made.append("Enhanced skills alignment with job requirements")
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "Resume optimized successfully",
                "original_score": round(original_ats.overall_score, 2),
                "optimized_score": round(optimized_ats.overall_score, 2),
                "score_improvement": round(optimized_ats.overall_score - original_ats.overall_score, 2),
                "optimized_file": optimized_filename,
                "improvements_made": improvements_made,
                "keywords_added": list(set(optimized_ats.keyword_analysis.matched_keywords) - set(original_ats.keyword_analysis.matched_keywords)),
                "match_level": optimized_ats.match_level,
                "critical_improvements_remaining": optimized_ats.critical_improvements
            }
        )
    
    except Exception as e:
        logger.error(f"Failed to optimize resume for user {request.user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unable to optimize resume, {str(e)}"
        )


@app.get('/resume/{user_id}')
async def get_resume(user_id: str):
    """Retrieve user's stored resume"""
    if not user_id or not user_id.strip():
        raise HTTPException(status_code=400, detail="User ID cannot be empty")
    
    data = load_data(FILE_PATH)
    
    if user_id not in data:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        resume_yaml = data[user_id]['resume_yaml']
        return JSONResponse(
            status_code=200,
            content={
                "user_id": user_id,
                "resume_yaml": resume_yaml
            }
        )
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail="Resume not found for this user"
        )