"""
chains.py
=========
LangChain chain builder functions for the BYOK (Bring-Your-Own-Key) model.

Each public function accepts a pre-built LangChain LLM instance and returns
a ready-to-invoke chain. Chains are constructed per-request — there are no
module-level globals that hold a specific provider's key.

A module-level `llm` fallback is still built from the server .env (if present)
and used only by the /health endpoint to check if a default model is reachable.
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from config.prompts import (
    res2yaml_system_prompt,
    jd_template,
    ats_calculation,
    ENHANCED_ATS_SYSTEM_PROMPT,
    ENHANCED_ATS_HUMAN_TEMPLATE,
    OPTIMIZATION_SYSTEM_PROMPT,
    OPTIMIZATION_HUMAN_TEMPLATE,
)
from schema.schema import parsedJobDescription, ATS, DetailedATS
from dotenv import load_dotenv
import os
import logging

load_dotenv()
logger = logging.getLogger("Sculpt")


# ──────────────────────────────────────────────────────────────────────────────
# Module-level fallback LLM (used only by /health, loaded from .env if present)
# ──────────────────────────────────────────────────────────────────────────────
_fallback_openai_key = os.getenv("OPENAI_API_KEY")
_fallback_groq_key   = os.getenv("GROQ_API_KEY")

# `llm` is kept for backward compatibility with main.py /health endpoint
if _fallback_openai_key:
    llm = ChatOpenAI(api_key=_fallback_openai_key, model="gpt-4o-mini")
else:
    llm = None
    logger.warning(
        "[BYOK] No OPENAI_API_KEY in .env — /health model_loaded will be False. "
        "This is expected in BYOK mode."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Chain builder functions — call these with a user-supplied LLM instance
# ──────────────────────────────────────────────────────────────────────────────

def build_res2yaml_chain(llm):
    """Resume text → structured YAML chain."""
    prompt = ChatPromptTemplate(
        [
            ("system", res2yaml_system_prompt),
            ("human", "{resume_content}"),
        ]
    )
    return prompt | llm


def build_jd_parser_chain(llm):
    """Raw job description text → parsedJobDescription structured output chain."""
    jd_parser_llm = llm.with_structured_output(parsedJobDescription)
    jd_prompt = PromptTemplate(
        template=jd_template,
        input_variables=["job_description"],
    )
    return jd_prompt | jd_parser_llm


def build_ats_chain(llm):
    """(Legacy) Simple ATS score chain — kept for backward compatibility."""
    ats_llm = llm.with_structured_output(ATS)
    ats_prompt = PromptTemplate(
        template=ats_calculation,
        input_variables=["job_title", "skills", "job_description", "resume_yaml"],
    )
    return ats_prompt | ats_llm


def build_enhanced_ats_chain(llm):
    """Detailed ATS breakdown chain with sub-scores."""
    enhanced_ats_llm = llm.with_structured_output(DetailedATS)
    enhanced_ats_prompt = ChatPromptTemplate(
        [
            ("system", ENHANCED_ATS_SYSTEM_PROMPT),
            ("human", ENHANCED_ATS_HUMAN_TEMPLATE),
        ]
    )
    return enhanced_ats_prompt | enhanced_ats_llm


def build_optimization_chain(llm):
    """Resume optimization chain — returns raw LLM content (YAML string)."""
    optimization_prompt = ChatPromptTemplate(
        [
            ("system", OPTIMIZATION_SYSTEM_PROMPT),
            ("human", OPTIMIZATION_HUMAN_TEMPLATE),
        ]
    )
    return optimization_prompt | llm