"""
latex_generator.py
==================
Modular, high-fidelity LaTeX resume templating and compilation engine.
Replaces legacy ReportLab canvas generation.

Public API:
- render_latex_template(resume_input, template_name="jakes_resume") -> str
- compile_latex_to_pdf(tex_source, timeout_seconds=45) -> bytes
- generate_pdf_from_resume(resume_input, template="jakes_resume") -> bytes
"""

import os
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from config.latex_sanitizer import latex_escape, normalize_resume_data

logger = logging.getLogger("Sculpt")

# Directory paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BACKEND_DIR / "templates" / "latex"
BIN_DIR = BACKEND_DIR / "bin"

SUPPORTED_TEMPLATES = {
    "jakes_resume": "jakes_resume.tex.j2",
    "classic_ivy": "classic_ivy.tex.j2",
    "compact_onepage": "compact_onepage.tex.j2",
}

# ── Jinja2 Environment ────────────────────────────────────────────────────────

def _create_jinja_env() -> Environment:
    """Create a Jinja2 environment configured for LaTeX templates."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        comment_start_string="/*%",
        comment_end_string="%*/",
    )
    # Register LaTeX escaping filter
    env.filters["escape_tex"] = latex_escape
    env.filters["tex"] = latex_escape
    return env

_jinja_env: Optional[Environment] = None

def get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = _create_jinja_env()
    return _jinja_env


# ── Tectonic Binary Discovery ─────────────────────────────────────────────────

def find_tectonic_binary() -> str:
    """Locate the Tectonic compiler binary.

    Searches:
    1. Local backend/bin/tectonic.exe or backend/bin/tectonic
    2. System PATH (via shutil.which)
    """
    local_candidates = [
        BIN_DIR / "tectonic.exe",
        BIN_DIR / "tectonic",
    ]
    for candidate in local_candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)

    which_path = shutil.which("tectonic")
    if which_path:
        return which_path

    # Windows fallback check without X_OK
    for candidate in local_candidates:
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        "Tectonic executable not found. Please install Tectonic or place it in backend/bin/."
    )


# ── Custom Exceptions ────────────────────────────────────────────────────────

class LaTeXCompilationError(Exception):
    """Raised when LaTeX / Tectonic compilation fails."""
    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


# ── Core Functions ───────────────────────────────────────────────────────────

def render_latex_template(
    resume_input: Union[Dict[str, Any], str],
    template_name: str = "jakes_resume",
) -> str:
    """Render structured resume data into a sanitized LaTeX (.tex) string.

    Parameters
    ----------
    resume_input : dict | str
        Dictionary containing resume data or YAML string.
    template_name : str
        Template identifier: 'jakes_resume', 'classic_ivy', or 'compact_onepage'.

    Returns
    -------
    str : Valid, sanitized LaTeX source code.
    """
    if isinstance(resume_input, str):
        try:
            raw_data = yaml.safe_load(resume_input) or {}
        except Exception as e:
            logger.warning(f"Failed to parse resume YAML string: {e}")
            raw_data = {}
    elif isinstance(resume_input, dict):
        raw_data = resume_input
    else:
        raw_data = {}

    normalized_data = normalize_resume_data(raw_data)

    template_file = SUPPORTED_TEMPLATES.get(template_name, SUPPORTED_TEMPLATES["jakes_resume"])
    env = get_jinja_env()
    template = env.get_template(template_file)

    rendered_tex = template.render(
        resume=normalized_data,
        template_name=template_name,
    )
    return rendered_tex


def compile_latex_to_pdf(tex_source: str, timeout_seconds: int = 45) -> bytes:
    """Compile LaTeX source string into PDF bytes using Tectonic.

    Parameters
    ----------
    tex_source : str
        LaTeX source code to compile.
    timeout_seconds : int
        Maximum execution time allowed for compilation.

    Returns
    -------
    bytes : Compiled PDF document bytes.

    Raises
    ------
    LaTeXCompilationError : If compilation fails or times out.
    """
    tectonic_bin = find_tectonic_binary()

    with tempfile.TemporaryDirectory(prefix="sculpt_tectonic_") as tmpdir:
        input_tex = Path(tmpdir) / "resume.tex"
        output_pdf = Path(tmpdir) / "resume.pdf"

        input_tex.write_text(tex_source, encoding="utf-8")

        cmd = [
            tectonic_bin,
            "--chatter=minimal",
            "--outdir", str(tmpdir),
            str(input_tex),
        ]

        try:
            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(f"Tectonic compilation timed out after {timeout_seconds}s")
            raise LaTeXCompilationError(
                f"LaTeX compilation timed out after {timeout_seconds} seconds."
            ) from exc
        except Exception as e:
            logger.error(f"Failed to invoke Tectonic binary: {e}")
            raise LaTeXCompilationError(f"Failed to run Tectonic compiler: {e}") from e

        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "Unknown compiler error").strip()
            # Log full details on server
            logger.error(f"Tectonic compilation failed (code {result.returncode}):\n{err_msg}")
            raise LaTeXCompilationError(
                f"LaTeX compilation error: {err_msg[:300]}",
                stdout=result.stdout,
                stderr=result.stderr,
            )

        if not output_pdf.is_file():
            logger.error("Tectonic succeeded but output PDF was not found.")
            raise LaTeXCompilationError("Output PDF was not generated by Tectonic.")

        return output_pdf.read_bytes()


def generate_pdf_from_resume(
    resume_input: Union[Dict[str, Any], str],
    template: str = "jakes_resume",
) -> bytes:
    """End-to-end PDF generation from resume data or YAML string.

    Renders LaTeX via Jinja2 and compiles with Tectonic.
    Falls back to ReportLab if Tectonic fails unexpectedly.
    """
    try:
        tex_source = render_latex_template(resume_input, template_name=template)
        return compile_latex_to_pdf(tex_source)
    except Exception as e:
        logger.warning(
            f"LaTeX / Tectonic generation failed ({e}). Falling back to ReportLab generator..."
        )
        try:
            from config.pdf_generator import generate_pdf_from_yaml_string
            yaml_str = (
                resume_input
                if isinstance(resume_input, str)
                else yaml.dump(resume_input, sort_keys=False, allow_unicode=True)
            )
            return generate_pdf_from_yaml_string(yaml_str)
        except Exception as fallback_err:
            logger.error(f"ReportLab fallback also failed: {fallback_err}")
            raise e
