"""
resume_functions.py
===================
High-level resume processing functions.

All functions accept a `llm` parameter — a LangChain BaseChatModel built by
`models.llm_factory.build_llm()` from the user's provider + API key.
"""

from models.chains import (
    build_jd_parser_chain,
    build_enhanced_ats_chain,
    build_optimization_chain,
    build_ats_chain,
    build_explain_changes_chain,
)
from schema.schema import parsedJobDescription
from config.prompts import TEMPLATE_GUIDANCE


async def ats(resume_content: str, job_description: parsedJobDescription, llm):
    """(Legacy) Simple ATS score — kept for backward compatibility."""
    chain = build_ats_chain(llm)
    response = await chain.ainvoke(
        input={
            "job_title":       job_description.job_title,
            "skills":          job_description.skills,
            "job_description": job_description.job_description,
            "resume_yaml":     resume_content,
        }
    )
    return response


async def ats_detailed(resume_content: str, job_description: parsedJobDescription, llm):
    """Enhanced ATS calculation with detailed breakdown."""
    chain = build_enhanced_ats_chain(llm)
    response = await chain.ainvoke(
        input={
            "job_title":       job_description.job_title,
            "skills":          ", ".join(job_description.skills),
            "job_description": job_description.job_description,
            "resume_yaml":     resume_content,
        }
    )
    return response


async def parse_jd(job_description: str, llm):
    """Parse job description to extract key information."""
    chain = build_jd_parser_chain(llm)
    response = await chain.ainvoke(
        input={"job_description": job_description},
    )
    return response


async def optimize_resume(
    resume_content: str,
    job_description,
    llm,
    addons: str = "",
    template: str = "jakes_resume",
):
    """Optimize resume based on job description, optional addons, and template layout guidance.

    After LLM generation, a hard guardrail restores the education section
    verbatim from the original resume so it can never be altered by the model.
    """
    import yaml

    guidance = TEMPLATE_GUIDANCE.get(template, TEMPLATE_GUIDANCE["jakes_resume"])
    chain = build_optimization_chain(llm)
    response = await chain.ainvoke(
        input={
            "job_title":          job_description.job_title,
            "skills":             ", ".join(job_description.skills),
            "job_description":    job_description.job_description,
            "resume_yaml":        resume_content,
            "addons":             addons if addons else "No additional information provided.",
            "template_guidance":  guidance,
        }
    )

    optimized_yaml = response.content

    # ---- Strip markdown fences ----
    if "```" in optimized_yaml:
        optimized_yaml = (
            optimized_yaml.split("```yaml")[-1]
            if "```yaml" in optimized_yaml
            else optimized_yaml.split("```")[-1]
        )
        optimized_yaml = optimized_yaml.split("```")[0]
        optimized_yaml = optimized_yaml.strip()

    # ---- Hard guardrail: restore education from original ----
    try:
        original_data  = yaml.safe_load(resume_content)
        optimized_data = yaml.safe_load(optimized_yaml)

        if isinstance(original_data, dict) and isinstance(optimized_data, dict):
            original_education = original_data.get("education")

            if original_education is not None:
                # Overwrite whatever the LLM produced (or inject if missing)
                optimized_data["education"] = original_education
                optimized_yaml = yaml.dump(
                    optimized_data,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
    except Exception:
        # If YAML parsing fails for any reason, return as-is and let
        # the caller handle the raw string (same behaviour as before).
        pass

    return optimized_yaml


async def explain_resume_changes(
    original_yaml: str,
    optimized_yaml: str,
    job_description: str,
    llm,
):
    """Generate structured explanations of resume changes and strategic rationale."""
    chain = build_explain_changes_chain(llm)
    response = await chain.ainvoke(
        input={
            "job_description": job_description if job_description else "Target software engineering role",
            "original_resume": original_yaml,
            "optimized_resume": optimized_yaml,
        }
    )
    return response


async def optimize_resume_agentic(
    resume_content: str,
    job_description,
    llm,
    original_ats_score: float = None,
    template: str = "jakes_resume",
    target_page_count: int = None,
    addons: str = "",
):
    """Runs the LangGraph multi-node optimization and validation workflow."""
    from models.workflow import run_resume_optimization_workflow

    inputs = {
        "user_id": "",
        "original_resume_yaml": resume_content,
        "target_job_description": getattr(job_description, "job_description", str(job_description)),
        "parsed_jd": job_description,
        "selected_template": template,
        "target_page_count": target_page_count or (1 if template == "compact_onepage" else 2),
        "llm": llm,
        "addons": addons,
        "original_ats_score": original_ats_score or 0.0,
    }

    return await run_resume_optimization_workflow(inputs)