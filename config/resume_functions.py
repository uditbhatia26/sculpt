from models.chains import (
    ats_chain, 
    jd_parser_chain, 
    enhanced_ats_chain, 
    optimization_chain
)

async def ats(resume_content: str, job_description):
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

async def ats_detailed(resume_content: str, job_description):
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
    """Optimize resume based on job description and optional addons"""
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
    
    # Clean up markdown
    if "```" in optimized_yaml:
        optimized_yaml = optimized_yaml.split("```yaml")[-1] if "```yaml" in optimized_yaml else optimized_yaml.split("```")[-1]
        optimized_yaml = optimized_yaml.split("```")[0]
        optimized_yaml = optimized_yaml.strip()
    
    return optimized_yaml