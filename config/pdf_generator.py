"""
config/pdf_generator.py
-----------------------
Pure-Python PDF resume generator using ReportLab.
No system dependencies — works on every cloud provider.

Public API:
    generate_pdf_from_yaml_string(yaml_str: str) -> bytes
"""

import io
import yaml

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, HRFlowable, KeepTogether,
)

# ── Colour palette ──────────────────────────────────────────────────────────
ACCENT    = colors.HexColor("#2563EB")
TEXT_DARK = colors.HexColor("#0F172A")
TEXT_MID  = colors.HexColor("#475569")
DIVIDER   = colors.HexColor("#CBD5E1")


# ── Styles ──────────────────────────────────────────────────────────────────

def _build_styles() -> dict:
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


# ── Helpers ─────────────────────────────────────────────────────────────────

def _divider():
    return HRFlowable(width="100%", thickness=0.5, color=DIVIDER, spaceAfter=4)


def _section(title: str, styles: dict) -> list:
    return [Paragraph(title.upper(), styles["section"]), _divider()]


def _bullets(items: list, styles: dict) -> list:
    return [Paragraph(f"• {item}", styles["bullet"]) for item in items]


def _role_row(left: str, right: str, styles: dict):
    """Two-column table: left = role/name, right = date/location (right-aligned)."""
    t = Table(
        [[Paragraph(left, styles["role"]), Paragraph(right, styles["meta"])]],
        colWidths=["75%", "25%"],
    )
    t.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("ALIGN",        (1, 0), (1,  0),  "RIGHT"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    return t


def _mid_hex() -> str:
    """Return TEXT_MID as a 6-char hex string for inline ReportLab markup."""
    return TEXT_MID.hexval()[2:]   # strip leading "FF" alpha byte


# ── Content builder ─────────────────────────────────────────────────────────

def _build_content(data: dict, styles: dict) -> list:
    story = []
    mid   = _mid_hex()

    # Header
    story.append(Paragraph(data.get("name", ""), styles["name"]))
    contact = data.get("contact", {})
    parts   = [
        str(contact[f]) for f in ["location", "email", "phone", "linkedin", "github"]
        if contact.get(f)
    ]
    story.append(Paragraph("  |  ".join(parts), styles["contact"]))
    story.append(_divider())

    # Experience
    for job in data.get("experience", []):
        loc_period = " · ".join(filter(None, [job.get("location"), job.get("period")]))
        block = [
            _role_row(
                f"<b>{job.get('role','')}</b>  <font color='#{mid}'>{job.get('company','')}</font>",
                loc_period, styles,
            ),
            Spacer(1, 3),
        ] + _bullets(job.get("achievements", []), styles) + [Spacer(1, 5)]
        story.append(KeepTogether(block))
    if data.get("experience"):
        story = _section("Experience", styles) + story[1:]  # prepend heading

    # rebuild properly — just prepend sections before their blocks
    story = []

    def add_section(title, blocks):
        if blocks:
            story.extend(_section(title, styles))
            story.extend(blocks)

    # Experience
    exp_blocks = []
    for job in data.get("experience", []):
        loc_period = " · ".join(filter(None, [job.get("location"), job.get("period")]))
        block = [
            _role_row(
                f"<b>{job.get('role','')}</b>  <font color='#{mid}'>{job.get('company','')}</font>",
                loc_period, styles,
            ),
            Spacer(1, 3),
        ] + _bullets(job.get("achievements", []), styles) + [Spacer(1, 5)]
        exp_blocks.append(KeepTogether(block))
    add_section("Experience", exp_blocks)

    # Projects
    proj_blocks = []
    for proj in data.get("projects", []):
        stack = " · ".join(proj.get("stack", []))
        block = [
            _role_row(
                f"<b>{proj.get('name','')}</b>  <i><font color='#{mid}'>{proj.get('description','')}</font></i>",
                f"<font color='#2563EB'>{stack}</font>", styles,
            ),
            Spacer(1, 3),
        ] + _bullets(proj.get("highlights", []), styles) + [Spacer(1, 5)]
        proj_blocks.append(KeepTogether(block))
    add_section("Projects", proj_blocks)

    # Education
    edu_blocks = []
    for edu in data.get("education", []):
        degree_str = edu.get("degree", "")
        if edu.get("major"):
            degree_str += f" — {edu['major']}"
        right = " · ".join(filter(None, [edu.get("period"), f"CGPA: {edu['cgpa']}" if edu.get("cgpa") else None]))
        block = [
            _role_row(
                f"<b>{edu.get('institution','')}</b>  <font color='#{mid}'>{degree_str}</font>",
                right, styles,
            ),
            Spacer(1, 6),
        ]
        edu_blocks.append(KeepTogether(block))
    add_section("Education", edu_blocks)

    # Technical Skills
    tech = data.get("technical_skills", {})
    if tech:
        skill_map = [
            ("Languages",     "programming_languages"),
            ("Domains",       "domains"),
            ("Frameworks",    "tools_and_frameworks"),
            ("Web / Backend", "web_and_backend"),
            ("Databases",     "databases"),
        ]
        rows = [
            [Paragraph(label, styles["skill_label"]),
             Paragraph(", ".join(tech[key]), styles["skill_value"])]
            for label, key in skill_map
            if tech.get(key)
        ]
        if rows:
            story.extend(_section("Technical Skills", styles))
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

    # Certifications
    certs = data.get("certifications", [])
    if certs:
        story.extend(_section("Certifications", styles))
        story += [Paragraph(f"• {c}", styles["cert"]) for c in certs]
        story.append(Spacer(1, 6))

    # Extracurricular
    extra_blocks = []
    for act in data.get("extracurricular_activities", []):
        block = [
            _role_row(
                f"<b>{act.get('role','')}</b>  <font color='#{mid}'>{act.get('organization','')}</font>",
                act.get("period", ""), styles,
            ),
            Spacer(1, 3),
        ] + _bullets(act.get("highlights", []), styles) + [Spacer(1, 5)]
        extra_blocks.append(KeepTogether(block))
    add_section("Leadership & Extracurriculars", extra_blocks)

    return story


# ── Public API ───────────────────────────────────────────────────────────────

def generate_pdf_from_yaml_string(yaml_str: str) -> bytes:
    """
    Accept a YAML string (as stored in the DB / session), return PDF bytes.
    Ready to be streamed directly from a FastAPI endpoint.
    """
    data   = yaml.safe_load(yaml_str)
    styles = _build_styles()

    # Build header separately so it always appears first
    mid   = _mid_hex()
    header = []
    header.append(Paragraph(data.get("name", ""), styles["name"]))
    contact = data.get("contact", {})
    parts   = [
        str(contact[f]) for f in ["location", "email", "phone", "linkedin", "github"]
        if contact.get(f)
    ]
    header.append(Paragraph("  |  ".join(parts), styles["contact"]))
    header.append(_divider())

    body  = _build_content(data, styles)
    story = header + body

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.4 * cm,
    )
    doc.build(story)
    return buf.getvalue()
