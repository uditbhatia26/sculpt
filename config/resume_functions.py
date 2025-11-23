from models.chains import ats_chain, jd_parser_chain

async def ats(resume_content: str, job_description):
    """Based on the given job description score the given resume"""
    response = await ats_chain.ainvoke(
        input={
            'job_title': job_description.job_title,
            'skills': job_description.skills,
            'job_description': job_description.job_description,
            'resume_yaml': resume_content
        }
    )

    return response


async def parse_jd(job_description: str):
    response = await jd_parser_chain.ainvoke(
        input = {
            'job_description': job_description
        },
    )
    return response