"""
Compile every resume template once, at Docker build time.

Tectonic downloads the LaTeX packages and fonts a document needs the first time it
compiles it, and caches them. Running this during the image build puts that cache inside
the image, so the first resume after a deploy or restart doesn't wait on (or fail on) a
download. It also proves Tectonic works in the image: any failure stops the build instead
of shipping a server whose PDFs silently fall back to the plain ReportLab layout.

    python scripts/warm_tectonic.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.latex_generator import SUPPORTED_TEMPLATES, compile_latex_to_pdf, render_latex_template  # noqa: E402

# Every section the templates can draw, including the characters that need escaping
SAMPLE = {
    "name": "Jane Doe",
    "contact": {
        "phone": "+91 98765 43210",
        "email": "jane.doe@example.com",
        "location": "Bengaluru, India",
        "linkedin": "linkedin.com/in/janedoe",
        "github": "github.com/janedoe",
        "portfolio": "janedoe.dev",
    },
    "summary": "Backend engineer who cut API latency by 40% & shipped R&D tools used by 10,000+ people.",
    "education": [{
        "institution": "Example Institute of Technology", "location": "Delhi, India",
        "degree": "B.Tech", "major": "Computer Science", "cgpa": "8.9/10",
        "period": "2019 - 2023", "honors": ["Dean's List"],
    }],
    "experience": [{
        "company": "Acme Corp", "role": "Software Engineer", "period": "2023 - Present",
        "location": "Remote",
        "achievements": [
            "Built a FastAPI service handling 1M+ requests/day with p99 under 120 ms",
            "Moved C++ & Python jobs to a queue, saving $5K a month",
        ],
    }],
    "projects": [{
        "name": "Sculpt", "description": "Resume tailoring", "period": "2026",
        "stack": ["Python", "React", "PostgreSQL"],
        "highlights": ["Wrote a LangGraph pipeline with ATS checks", "Rendered PDFs with LaTeX"],
        "links": {"github": "github.com/janedoe/sculpt"},
    }],
    "technical_skills": {
        "languages": ["Python", "TypeScript", "C++"],
        "frameworks": ["FastAPI", "React"],
        "tools": ["Docker", "Git"],
        "databases": ["PostgreSQL"],
    },
    "certifications": [{"name": "AWS Certified Developer", "issuer": "Amazon"}],
    "extracurricular_activities": [{
        "organization": "Coding Club", "role": "Lead", "period": "2021 - 2022",
        "highlights": ["Ran weekly workshops for 80 students"],
    }],
}


def main() -> int:
    for template in SUPPORTED_TEMPLATES:
        pdf = compile_latex_to_pdf(render_latex_template(SAMPLE, template_name=template), timeout_seconds=300)
        if not pdf.startswith(b"%PDF"):
            print(f"[warm] {template}: Tectonic returned something that isn't a PDF", file=sys.stderr)
            return 1
        print(f"[warm] {template}: OK ({len(pdf):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
