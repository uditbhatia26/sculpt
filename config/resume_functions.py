from models.chains import (
    ats_chain, 
    jd_parser_chain, 
    enhanced_ats_chain, 
    optimization_chain
)
from schema.schema import parsedJobDescription

async def ats(resume_content: str, job_description: parsedJobDescription):
    """Based on the given job description score the given resume (OLD VERSION)"""
    response = await ats_chain.ainvoke(
        input={
            'job_title': job_description.job_title,
            'skills': job_description.skills,
            'job_description': job_description.job_description,
            'resume_yaml': resume_content
        }
    )
    return response

async def ats_detailed(resume_content: str, job_description: parsedJobDescription):
    """Enhanced ATS calculation with detailed breakdown"""
    response = await enhanced_ats_chain.ainvoke(
        input={
            'job_title': job_description.job_title,
            'skills': ', '.join(job_description.skills),
            'job_description': job_description.job_description,
            'resume_yaml': resume_content
        }
    )
    return response

async def parse_jd(job_description: str):
    """Parse job description to extract key information"""
    response = await jd_parser_chain.ainvoke(
        input = {
            'job_description': job_description
        },
    )
    return response

async def optimize_resume(resume_content: str, job_description, addons: str = ""):
    """Optimize resume based on job description and optional addons.

    After LLM generation, a hard guardrail restores the education section
    verbatim from the original resume so it can never be altered by the model.
    """
    import yaml

    response = await optimization_chain.ainvoke(
        input={
            'job_title': job_description.job_title,
            'skills': ', '.join(job_description.skills),
            'job_description': job_description.job_description,
            'resume_yaml': resume_content,
            'addons': addons if addons else "No additional information provided."
        }
    )

    optimized_yaml = response.content

    # ---- Strip markdown fences ----
    if "```" in optimized_yaml:
        optimized_yaml = optimized_yaml.split("```yaml")[-1] if "```yaml" in optimized_yaml else optimized_yaml.split("```")[-1]
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