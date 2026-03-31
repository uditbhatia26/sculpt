"""
demo_reportlab.py
-----------------
Generates a professional PDF resume from a YAML file using ReportLab.
Pure Python — no system dependencies, no external binaries.
Works identically on Windows, Linux, macOS, and every cloud provider.

Usage (from backend/):
    uv run python demo_reportlab.py
"""

import os
import yaml
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.platypus.flowables import HRFlowable

# ── Paths ──────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent.resolve()
YAML_SOURCE = BACKEND_DIR / "debug_resumes" / "resume_2261ec2e-b473-4bd3-8427-d25dee10b036_20260314_161151.yaml"
OUTPUT_PDF  = BACKEND_DIR / "demo_reportlab_output.pdf"

# ── Colour palette ──────────────────────────────────────────────────────────
ACCENT     = colors.HexColor("#2563EB")   # blue headings
TEXT_DARK  = colors.HexColor("#0F172A")   # near-black body
TEXT_MID   = colors.HexColor("#475569")   # grey secondary
DIVIDER    = colors.HexColor("#CBD5E1")   # light rule
BG_LIGHT   = colors.HexColor("#F8FAFC")   # barely-there header bg


def build_styles():
    """Return a dict of all paragraph styles used in the resume."""
    base = getSampleStyleSheet()
    return {
        "name": ParagraphStyle(
            "Name",
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=TEXT_DARK,
            alignment=TA_CENTER,
            spaceAfter=15,
        ),
        "contact": ParagraphStyle(
            "Contact",
            fontName="Helvetica",
            fontSize=8.5,
            textColor=TEXT_MID,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "Section",
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=ACCENT,
            spaceBefore=10,
            spaceAfter=2,
        ),
        "role": ParagraphStyle(
            "Role",
            fontName="Helvetica-Bold",
            fontSize=9.5,
            textColor=TEXT_DARK,
            spaceBefore=4,
            spaceAfter=1,
        ),
        "company": ParagraphStyle(
            "Company",
            fontName="Helvetica-Oblique",
            fontSize=9,
            textColor=TEXT_MID,
            spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "Meta",
            fontName="Helvetica",
            fontSize=8,
            textColor=TEXT_MID,
            alignment=TA_RIGHT,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            fontName="Helvetica",
            fontSize=9,
            textColor=TEXT_DARK,
            leftIndent=10,
            bulletIndent=0,
            spaceAfter=1.5,
            leading=12,
        ),
        "skill_label": ParagraphStyle(
            "SkillLabel",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=TEXT_MID,
        ),
        "skill_value": ParagraphStyle(
            "SkillValue",
            fontName="Helvetica",
            fontSize=9,
            textColor=TEXT_DARK,
            leading=12,
        ),
        "cert": ParagraphStyle(
            "Cert",
            fontName="Helvetica",
            fontSize=9,
            textColor=TEXT_DARK,
            leftIndent=10,
            spaceAfter=2,
        ),
    }


def divider():
    """Thin horizontal rule matching the accent colour."""
    return HRFlowable(
        width="100%", thickness=0.5, color=DIVIDER, spaceAfter=4
    )


def section_heading(title: str, styles: dict):
    return [
        Paragraph(title.upper(), styles["section"]),
        divider(),
    ]


def bullet_items(items: list, styles: dict):
    """Convert a list of strings into bullet paragraphs."""
    return [
        Paragraph(f"• {item}", styles["bullet"])
        for item in items
    ]


def role_row(left: str, right: str, styles: dict):
    """Two-column row: role/company on the left, date/location on the right."""
    t = Table(
        [[Paragraph(left, styles["role"]), Paragraph(right, styles["meta"])]],
        colWidths=["75%", "25%"],
    )
    t.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "TOP"),
        ("ALIGN",   (1, 0), (1, 0),  "RIGHT"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    return t


def build_content(data: dict, styles: dict) -> list:
    """Build the full list of ReportLab flowables from parsed YAML data."""
    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    story.append(Paragraph(data.get("name", ""), styles["name"]))

    contact = data.get("contact", {})
    contact_parts = []
    for field in ["location", "email", "phone", "linkedin", "github"]:
        if field in contact and contact[field]:
            contact_parts.append(str(contact[field]))
    story.append(Paragraph("  |  ".join(contact_parts), styles["contact"]))
    story.append(divider())

    # ── Experience ──────────────────────────────────────────────────────────
    experience = data.get("experience", [])
    if experience:
        story += section_heading("Experience", styles)
        for job in experience:
            loc_period = []
            if job.get("location"): loc_period.append(job["location"])
            if job.get("period"):   loc_period.append(job["period"])

            block = [
                role_row(
                    f"<b>{job.get('role','')}</b>  <font color='#{TEXT_MID.hexval()[2:]}'>{job.get('company','')}</font>",
                    " · ".join(loc_period),
                    styles,
                ),
                Spacer(1, 3),
            ] + bullet_items(job.get("achievements", []), styles) + [Spacer(1, 5)]
            story.append(KeepTogether(block))

    # ── Projects ────────────────────────────────────────────────────────────
    projects = data.get("projects", [])
    if projects:
        story += section_heading("Projects", styles)
        for proj in projects:
            stack_str = " · ".join(proj.get("stack", []))
            name_line = f"<b>{proj.get('name','')}</b>  <i><font color='#{TEXT_MID.hexval()[2:]}'>{proj.get('description','')}</font></i>"
            block = [
                role_row(name_line, f"<font color='#2563EB'>{stack_str}</font>", styles),
                Spacer(1, 3),
            ] + bullet_items(proj.get("highlights", []), styles) + [Spacer(1, 5)]
            story.append(KeepTogether(block))

    # ── Education ───────────────────────────────────────────────────────────
    education = data.get("education", [])
    if education:
        story += section_heading("Education", styles)
        for edu in education:
            parts = [edu.get("degree", "")]
            if edu.get("major"):
                parts.append(f"— {edu['major']}")
            right_parts = []
            if edu.get("period"): right_parts.append(edu["period"])
            if edu.get("cgpa"):   right_parts.append(f"CGPA: {edu['cgpa']}")

            block = [
                role_row(
                    f"<b>{edu.get('institution','')}</b>  <font color='#{TEXT_MID.hexval()[2:]}'>{' '.join(parts)}</font>",
                    " · ".join(right_parts),
                    styles,
                ),
                Spacer(1, 6),
            ]
            story.append(KeepTogether(block))

    # ── Technical Skills ────────────────────────────────────────────────────
    technical_skills = data.get("technical_skills", {})
    if technical_skills:
        story += section_heading("Technical Skills", styles)
        skill_map = [
            ("Languages",    "programming_languages"),
            ("Domains",      "domains"),
            ("Frameworks",   "tools_and_frameworks"),
            ("Web / Backend","web_and_backend"),
            ("Databases",    "databases"),
        ]
        rows = []
        for label, key in skill_map:
            if key in technical_skills and technical_skills[key]:
                rows.append([
                    Paragraph(label, styles["skill_label"]),
                    Paragraph(", ".join(technical_skills[key]), styles["skill_value"]),
                ])
        if rows:
            t = Table(rows, colWidths=[3.5 * cm, None])
            t.setStyle(TableStyle([
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 1),
            ]))
            story.append(t)
            story.append(Spacer(1, 6))

    # ── Certifications ──────────────────────────────────────────────────────
    certifications = data.get("certifications", [])
    if certifications:
        story += section_heading("Certifications", styles)
        story += [Paragraph(f"• {c}", styles["cert"]) for c in certifications]
        story.append(Spacer(1, 6))

    # ── Extracurricular ─────────────────────────────────────────────────────
    extras = data.get("extracurricular_activities", [])
    if extras:
        story += section_heading("Leadership & Extracurriculars", styles)
        for act in extras:
            block = [
                role_row(
                    f"<b>{act.get('role','')}</b>  <font color='#{TEXT_MID.hexval()[2:]}'>{act.get('organization','')}</font>",
                    act.get("period", ""),
                    styles,
                ),
                Spacer(1, 3),
            ] + bullet_items(act.get("highlights", []), styles) + [Spacer(1, 5)]
            story.append(KeepTogether(block))

    return story


def generate_resume_pdf(yaml_path: Path, output_path: Path) -> Path:
    """
    Public API — call this from the FastAPI endpoint.
    Accepts a YAML string or path, returns the output PDF path.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
    )

    styles = build_styles()
    story  = build_content(data, styles)
    doc.build(story)
    return output_path


# ── Demo entry point ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[1/2] Generating PDF from: {YAML_SOURCE.name}")
    generate_resume_pdf(YAML_SOURCE, OUTPUT_PDF)
    print(f"[2/2] Done → {OUTPUT_PDF}")
    os.startfile(OUTPUT_PDF)   # open in default PDF viewer (Windows)
