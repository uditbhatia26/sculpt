"""
test_latex_compilation.py
=========================
Verification and regression test suite for Jinja2 + LaTeX / Tectonic resume generator.

Tests:
1. Compilation of sample real-world resumes from debug_resumes/
2. All 3 templates: jakes_resume, classic_ivy, compact_onepage
3. Stress testing with LaTeX control characters (%, &, _, $, #, {}, ~, ^, <, >, "C++", "R&D", "$50K", "100%")
4. Valid PDF byte stream output validation (%PDF header)
"""

import glob
import os
import sys
from pathlib import Path

# Add backend to sys.path
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from config.latex_generator import (
    render_latex_template,
    compile_latex_to_pdf,
    generate_pdf_from_resume,
    SUPPORTED_TEMPLATES,
)
from config.latex_sanitizer import latex_escape, normalize_resume_data


def test_latex_escape():
    print("\n--- [Test 1] Testing LaTeX Character Escaping ---")
    raw_text = "R&D Engineer at 100% efficiency earning $50K with C++ & C# in {Engineering} ~team^ <special> under_score"
    escaped = latex_escape(raw_text)
    print("Raw:    ", raw_text)
    print("Escaped:", escaped)

    assert r"\&" in escaped, "Missing escaped &"
    assert r"\%" in escaped, "Missing escaped %"
    assert r"\$" in escaped, "Missing escaped $"
    assert r"\#" in escaped, "Missing escaped #"
    assert r"\{" in escaped, "Missing escaped {"
    assert r"\}" in escaped, "Missing escaped }"
    assert r"\_" in escaped, "Missing escaped _"
    print("[PASS] latex_escape passed all assertions.")


def test_special_characters_compilation():
    print("\n--- [Test 2] Testing Compilation With Problematic Special Characters ---")
    stress_resume = {
        "name": "Jane & John Doe, Ph.D. $100% Guaranteed",
        "contact": {
            "email": "jane_doe#1@domain.com",
            "phone": "+1-555-0199 & ext_123",
            "location": "New York, NY <Remote>",
            "linkedin": "linkedin.com/in/jane_doe_100%",
            "github": "github.com/janedoe$test",
        },
        "summary": "Experienced R&D software architect with 10+ years optimizing algorithms by 100% and saving $500K annually.",
        "experience": [
            {
                "company": "AT&T Labs & Bellcore",
                "role": "Lead C++ & Python Engineer #1",
                "period": "2020 – Present",
                "location": "Bedminster, NJ",
                "achievements": [
                    "Spearheaded R&D initiative utilizing C++, C#, and {microservices}, reducing latency by 45%.",
                    "Saved $1.2M+ through automated cloud infrastructure deployed via Docker & Kubernetes.",
                    "Handled 100% of customer telemetry pipelines processing 50M+ events/day with 99.999% uptime.",
                ],
            }
        ],
        "education": [
            {
                "institution": "University of Texas at Austin & A&M",
                "degree": "B.S. in Computer Science & Mathematics",
                "major": "Systems & AI/ML",
                "cgpa": "3.95 / 4.0 (Top 1%)",
                "period": "2016 – 2020",
                "location": "Austin, TX",
            }
        ],
        "projects": [
            {
                "name": "AlgoTrade_v2.0 #OpenSource",
                "description": "High-frequency trading bot in C++ & Rust delivering 150% alpha",
                "stack": ["C++", "Python", "PostgreSQL", "ZeroMQ"],
                "period": "2022",
                "highlights": [
                    "Engineered lock-free message queues processing orders in <15 microseconds.",
                    "Backtested across $10B+ volume datasets with zero missed packets.",
                ],
            }
        ],
        "technical_skills": {
            "programming_languages": ["C++", "C#", "Python", "SQL & PL/SQL"],
            "tools_and_frameworks": ["FastAPI", "Docker & Podman", "Git & GitHub"],
        },
        "certifications": [
            "AWS Certified Solutions Architect (100% Score)",
            {"name": "CKA: Certified Kubernetes Administrator & DevOps"},
        ],
    }

    for template_name in SUPPORTED_TEMPLATES.keys():
        print(f"  Testing template '{template_name}' with stress characters...")
        tex_source = render_latex_template(stress_resume, template_name=template_name)
        assert len(tex_source) > 200, f"Template '{template_name}' produced empty output"
        pdf_bytes = compile_latex_to_pdf(tex_source, timeout_seconds=60)
        assert pdf_bytes.startswith(b"%PDF"), f"Output for '{template_name}' is not a valid PDF"
        print(f"  [PASS] Template '{template_name}' compiled cleanly! PDF size: {len(pdf_bytes):,} bytes")


def test_sample_resumes_from_disk():
    print("\n--- [Test 3] Testing Real Sample Resumes from debug_resumes/ ---")
    yaml_files = glob.glob(str(BACKEND_DIR / "debug_resumes" / "*.yaml"))
    if not yaml_files:
        print("  No debug_resumes found on disk. Skipping.")
        return

    print(f"  Found {len(yaml_files)} real-world resume YAMLs.")
    sample_file = yaml_files[0]
    print(f"  Using: {Path(sample_file).name}")

    with open(sample_file, "r", encoding="utf-8") as f:
        yaml_content = f.read()

    for template_name in SUPPORTED_TEMPLATES.keys():
        print(f"  Compiling against template '{template_name}'...")
        pdf_bytes = generate_pdf_from_resume(yaml_content, template=template_name)
        assert pdf_bytes.startswith(b"%PDF"), f"Output for '{template_name}' did not produce a valid PDF"
        print(f"  [PASS] Template '{template_name}' produced valid PDF: {len(pdf_bytes):,} bytes")


def test_raw_tex_generation():
    print("\n--- [Test 4] Testing Raw .tex Source Export ---")
    yaml_files = glob.glob(str(BACKEND_DIR / "debug_resumes" / "*.yaml"))
    with open(yaml_files[0], "r", encoding="utf-8") as f:
        yaml_content = f.read()

    tex = render_latex_template(yaml_content, template_name="jakes_resume")
    assert r"\documentclass" in tex
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex
    print(f"  [PASS] Raw .tex generation verified ({len(tex):,} characters).")


if __name__ == "__main__":
    print("=" * 60)
    print("ResumeSculpt LaTeX Resume Engine Verification")
    print("=" * 60)
    test_latex_escape()
    test_special_characters_compilation()
    test_sample_resumes_from_disk()
    test_raw_tex_generation()
    print("\n" + "=" * 60)
    print("[SUCCESS] ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
