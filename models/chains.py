from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from config.prompts import (
    res2yaml_system_prompt, 
    jd_template, 
    ats_calculation,
    ENHANCED_ATS_SYSTEM_PROMPT,
    ENHANCED_ATS_HUMAN_TEMPLATE,
    OPTIMIZATION_SYSTEM_PROMPT,
    OPTIMIZATION_HUMAN_TEMPLATE
)
from schema.schema import parsedJobDescription, ATS, DetailedATS
from dotenv import load_dotenv
import os
load_dotenv()

MODEL_NAME = "openai/gpt-oss-120b"
llm = ChatGroq(api_key=os.getenv("GROQ_API_KEY"), model=MODEL_NAME)
openai_llm = ChatOpenAI(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o-mini")

# Resume to YAML conversion
prompt = ChatPromptTemplate(
    [
        ('system', res2yaml_system_prompt),
        ('human', "{resume_content}")
    ]
)
res2yaml_chain = prompt | llm

# Parsing the Job description
jd_parser_llm = llm.with_structured_output(parsedJobDescription)
jd_prompt = PromptTemplate(
    template=jd_template,
    input_variables=['job_description']
)
jd_parser_chain = jd_prompt | jd_parser_llm

# OLD ATS Calculation (Keep for backward compatibility)
ats_calulcation_llm = llm.with_structured_output(ATS)
ats_prompt = PromptTemplate(
    template=ats_calculation,
    input_variables=['job_title', 'skills', 'job_description', 'resume_yaml'],
)
ats_chain = ats_prompt | ats_calulcation_llm

# NEW Enhanced ATS Calculation with detailed breakdown
enhanced_ats_llm = llm.with_structured_output(DetailedATS)
enhanced_ats_prompt = ChatPromptTemplate(
    [
        ('system', ENHANCED_ATS_SYSTEM_PROMPT),
        ('human', ENHANCED_ATS_HUMAN_TEMPLATE)
    ]
)
enhanced_ats_chain = enhanced_ats_prompt | enhanced_ats_llm

# Resume Optimization Chain
optimization_prompt = ChatPromptTemplate(
    [
        ('system', OPTIMIZATION_SYSTEM_PROMPT),
        ('human', OPTIMIZATION_HUMAN_TEMPLATE)
    ]
)
optimization_chain = optimization_prompt | llm