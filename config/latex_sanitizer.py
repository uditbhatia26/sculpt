"""
latex_sanitizer.py
==================
Sanitization and data normalization utilities for LaTeX resume generation.

Provides:
- latex_escape(text): Escapes LaTeX control characters in user-supplied strings.
- normalize_resume_data(data): Cleans, validates, and standardizes resume dictionary structure
  so that LaTeX templates never encounter null pointer or unbalanced syntax errors.
"""

import re
from typing import Any, Dict, List, Optional, Union

# Mapping of LaTeX special characters to their escaped representations
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "$": r"\$",
    "#": r"\#",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "<": r"\textless{}",
    ">": r"\textgreater{}",
}

# Regex matching any LaTeX special character
_LATEX_ESCAPE_REGEX = re.compile(
    "|".join(re.escape(k) for k in _LATEX_ESCAPES.keys())
)


def latex_escape(value: Any) -> str:
    """Escape LaTeX special characters in a given string or value.

    If value is None or empty, returns empty string.
    If value is not a string, converts it to string before escaping.
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    return _LATEX_ESCAPE_REGEX.sub(lambda match: _LATEX_ESCAPES[match.group(0)], text)


def normalize_resume_data(raw_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize and sanitize raw resume data (from YAML or JSON) into a predictable structure.

    Ensures:
    - Default empty containers for missing sections
    - Clean string values without dangling None/nulls
    - Standardized skills mapping (languages, frameworks, tools, databases, etc.)
    - Consistent certifications representation (list of string titles)
    - Clean experience, projects, and education lists with string bullet items
    """
    if not isinstance(raw_data, dict):
        raw_data = {}

    normalized: Dict[str, Any] = {}

    # 1. Candidate Name
    normalized["name"] = str(raw_data.get("name") or "").strip()

    # 2. Contact Information
    contact_raw = raw_data.get("contact")
    if not isinstance(contact_raw, dict):
        contact_raw = {}

    normalized["contact"] = {
        "phone": str(contact_raw.get("phone") or "").strip(),
        "email": str(contact_raw.get("email") or "").strip(),
        "linkedin": str(contact_raw.get("linkedin") or "").strip(),
        "github": str(contact_raw.get("github") or "").strip(),
        "portfolio": str(contact_raw.get("portfolio") or contact_raw.get("website") or "").strip(),
        "location": str(contact_raw.get("location") or "").strip(),
    }

    # 3. Summary
    summary_raw = raw_data.get("summary")
    normalized["summary"] = str(summary_raw).strip() if summary_raw else ""

    # 4. Education
    education_list: List[Dict[str, Any]] = []
    for edu in raw_data.get("education") or []:
        if not isinstance(edu, dict):
            continue
        cgpa_val = edu.get("cgpa") or edu.get("gpa") or edu.get("percentage")
        education_list.append({
            "institution": str(edu.get("institution") or "").strip(),
            "degree": str(edu.get("degree") or "").strip(),
            "major": str(edu.get("major") or "").strip(),
            "minor": str(edu.get("minor") or "").strip(),
            "period": str(edu.get("period") or "").strip(),
            "location": str(edu.get("location") or "").strip(),
            "cgpa": str(cgpa_val).strip() if cgpa_val is not None else "",
            "honors": [str(h).strip() for h in (edu.get("honors") or []) if str(h).strip()],
            "notes": str(edu.get("notes") or "").strip(),
        })
    normalized["education"] = education_list

    # 5. Experience
    experience_list: List[Dict[str, Any]] = []
    for exp in raw_data.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        achievements = [
            str(a).strip()
            for a in (exp.get("achievements") or exp.get("highlights") or exp.get("responsibilities") or [])
            if str(a).strip()
        ]
        experience_list.append({
            "company": str(exp.get("company") or "").strip(),
            "role": str(exp.get("role") or "").strip(),
            "period": str(exp.get("period") or "").strip(),
            "location": str(exp.get("location") or "").strip(),
            "employment_type": str(exp.get("employment_type") or "").strip(),
            "achievements": achievements,
        })
    normalized["experience"] = experience_list

    # 6. Projects
    projects_list: List[Dict[str, Any]] = []
    for proj in raw_data.get("projects") or []:
        if not isinstance(proj, dict):
            continue
        stack_raw = proj.get("stack") or proj.get("technologies") or []
        stack_list = (
            [str(s).strip() for s in stack_raw if str(s).strip()]
            if isinstance(stack_raw, list)
            else [s.strip() for s in str(stack_raw).split(",") if s.strip()]
        )
        highlights = [
            str(h).strip()
            for h in (proj.get("highlights") or proj.get("achievements") or proj.get("bullets") or [])
            if str(h).strip()
        ]
        links = proj.get("links")
        github_link = ""
        live_link = ""
        if isinstance(links, dict):
            github_link = str(links.get("github") or "").strip()
            live_link = str(links.get("live") or links.get("demo") or "").strip()

        projects_list.append({
            "name": str(proj.get("name") or "").strip(),
            "description": str(proj.get("description") or "").strip(),
            "role": str(proj.get("role") or "").strip(),
            "period": str(proj.get("period") or "").strip(),
            "stack": stack_list,
            "stack_str": ", ".join(stack_list),
            "highlights": highlights,
            "github_link": github_link,
            "live_link": live_link,
        })
    normalized["projects"] = projects_list

    # 7. Technical Skills
    skills_raw = raw_data.get("technical_skills") or raw_data.get("skills")
    skill_groups: List[Dict[str, str]] = []

    if isinstance(skills_raw, dict):
        label_map = [
            ("programming_languages", "Languages"),
            ("languages",             "Languages"),
            ("tools_and_frameworks",  "Frameworks & Tools"),
            ("frameworks",            "Frameworks"),
            ("web_and_backend",       "Web & Backend"),
            ("databases",             "Databases"),
            ("cloud_and_devops",      "Cloud & DevOps"),
            ("domains",               "Domains"),
            ("developer_tools",       "Developer Tools"),
            ("libraries",             "Libraries"),
        ]

        seen_keys = set()
        for key, display_label in label_map:
            if key in skills_raw and skills_raw[key]:
                val = skills_raw[key]
                items = [str(i).strip() for i in val if str(i).strip()] if isinstance(val, list) else [str(val).strip()]
                if items:
                    skill_groups.append({
                        "category": display_label,
                        "skills": ", ".join(items)
                    })
                seen_keys.add(key)

        # Catch any remaining custom keys in the skills dict
        for k, v in skills_raw.items():
            if k not in seen_keys and v:
                display_label = k.replace("_", " ").title()
                items = [str(i).strip() for i in v if str(i).strip()] if isinstance(v, list) else [str(v).strip()]
                if items:
                    skill_groups.append({
                        "category": display_label,
                        "skills": ", ".join(items)
                    })
    elif isinstance(skills_raw, list):
        items = [str(i).strip() for i in skills_raw if str(i).strip()]
        if items:
            skill_groups.append({
                "category": "Skills",
                "skills": ", ".join(items)
            })

    normalized["skills"] = skill_groups

    # 8. Certifications
    certs_raw = raw_data.get("certifications") or []
    cert_list: List[str] = []
    for c in certs_raw:
        if isinstance(c, dict):
            name = c.get("name") or c.get("title") or c.get("certification") or ""
            issuer = c.get("issuer") or c.get("organization") or ""
            if issuer:
                cert_list.append(f"{name} ({issuer})" if name else issuer)
            elif name:
                cert_list.append(str(name).strip())
        elif isinstance(c, str) and c.strip():
            cert_list.append(c.strip())
    normalized["certifications"] = cert_list

    # 9. Extracurricular Activities / Leadership
    extra_raw = raw_data.get("extracurricular_activities") or raw_data.get("leadership") or []
    extra_list: List[Dict[str, Any]] = []
    for act in extra_raw:
        if not isinstance(act, dict):
            continue
        highlights = [
            str(h).strip()
            for h in (act.get("highlights") or act.get("achievements") or act.get("responsibilities") or [])
            if str(h).strip()
        ]
        extra_list.append({
            "organization": str(act.get("organization") or "").strip(),
            "role": str(act.get("role") or "").strip(),
            "period": str(act.get("period") or "").strip(),
            "location": str(act.get("location") or "").strip(),
            "highlights": highlights,
        })
    normalized["extracurricular"] = extra_list

    return normalized
