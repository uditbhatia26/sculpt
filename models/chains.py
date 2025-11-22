from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from config.prompts import res2yaml_system_prompt, jd_template
from schema.schema import parsedJobDescription
from dotenv import load_dotenv
import os
load_dotenv()


MODEL_NAME = "llama-3.3-70b-versatile"
groq_llm = ChatGroq(api_key=os.getenv("GROQ_API_KEY"), model=MODEL_NAME)
openai_llm = ChatOpenAI(api_key=os.getenv("OPENAI_API_KEY"), model="gpt-4o-mini")

prompt = ChatPromptTemplate(
        [
            ('system', res2yaml_system_prompt),
            ('human', "{resume_content}")
        ]
    )

res2yaml_chain = prompt | groq_llm

jd_parser_llm = groq_llm.with_structured_output(parsedJobDescription)
jd_prompt = PromptTemplate(
    template=jd_template,
    input_variables=['job_description']
)

jd_parser_chain = jd_prompt | jd_parser_llm