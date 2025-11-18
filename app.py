from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from schema.schema import JDRequest, parsedJobDescription, UserLogin, UserSignup
from dotenv import load_dotenv
from config.file_helpers import load_data, save_data
from models.job_desc import jd_parser_chain
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