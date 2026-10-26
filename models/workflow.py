"""
workflow.py
===========
LangGraph Multi-Node Optimization & Validation Pipeline for Sculpt.

Architecture:
- State: ResumeOptimizationState
- Nodes:
    1. node_ingest: Baseline ATS score & budget configuration
    2. node_draft: Template-guided generation + critique injection
    3. node_factual_guardrails: 0-token deterministic factual protection
    4. node_page_budget: In-memory Tectonic compilation & page budget enforcement
    5. node_ats_evaluator: ATS benchmarking and regression detection
    6. node_finalize: Best-effort candidate selector and diff engine
- Router: should_continue (evaluates budget + score regression vs max_retries)
"""

import copy
import io
import logging
from typing import TypedDict, Optional, List, Dict, Any

import yaml
from PyPDF2 import PdfReader
from langgraph.graph import StateGraph, START, END

from config.prompts import TEMPLATE_GUIDANCE
from config.latex_generator import render_latex_template, compile_latex_to_pdf
from models.chains import build_optimization_chain
from config.resume_functions import ats_detailed
from schema.schema import parsedJobDescription

logger = logging.getLogger("Sculpt")


# ── State Schema ─────────────────────────────────────────────────────────────

class ResumeOptimizationState(TypedDict, total=False):
    # Inputs
    user_id: str
    original_resume_yaml: str
    target_job_description: str
    parsed_jd: Any
    selected_template: str
    target_page_count: int
    llm: Any
    addons: str

    # Working drafts & outputs
    current_resume_yaml: str
    rendered_latex: Optional[str]
    compiled_pdf_bytes: Optional[bytes]
    actual_page_count: Optional[int]
    compile_error: Optional[str]

    # Scores & benchmarks
    original_ats_score: float
    current_ats_score: float
    current_ats_details: Optional[Dict[str, Any]]
    score_history: List[Dict[str, Any]]

    # Validation flags & feedback
    page_budget_passed: bool
    ats_score_passed: bool
    guardrails_passed: bool
    critique_notes: List[str]
    iteration_count: int
    max_retries: int
    is_satisfactory: bool

    # Final artifacts
    final_yaml: str
    final_latex: str
    keywords_added: List[str]
    improvements_made: List[str]


# ── Helper Utilities ─────────────────────────────────────────────────────────

def _normalize_parsed_jd(raw_jd: Any) -> parsedJobDescription:
    """Ensure parsed_jd is a valid parsedJobDescription schema instance."""
    if isinstance(raw_jd, parsedJobDescription):
        return raw_jd
    if isinstance(raw_jd, dict):
        return parsedJobDescription(
            job_title=raw_jd.get("job_title", ""),
            skills=raw_jd.get("skills", []),
            job_description=raw_jd.get("job_description", ""),
        )
    return parsedJobDescription(
        job_title=getattr(raw_jd, "job_title", ""),
        skills=getattr(raw_jd, "skills", []),
        job_description=getattr(raw_jd, "job_description", str(raw_jd)),
    )


def _target_pages(state: ResumeOptimizationState) -> int:
    """Page budget: explicit target if set, else 1 for compact_onepage and 2 otherwise."""
    explicit = state.get("target_page_count")
    if explicit:
        return explicit
    return 1 if state.get("selected_template") == "compact_onepage" else 2


def _page_ok(candidate: Dict[str, Any], target_pages: int) -> bool:
    """A candidate fits the page budget (unknown page count is given the benefit of the doubt)."""
    pc = candidate.get("page_count")
    return pc is None or pc <= target_pages


def _best_candidate(state: ResumeOptimizationState) -> Optional[Dict[str, Any]]:
    """Highest-scoring candidate so far, preferring ones inside the page budget."""
    history = state.get("score_history", [])
    if not history:
        return None
    target_pages = _target_pages(state)
    fitting = [c for c in history if _page_ok(c, target_pages)]
    return max(fitting or history, key=lambda c: c.get("score", 0.0))


def _flatten_text(yaml_str: str) -> str:
    """Lower-cased text of a resume for keyword presence checks."""
    try:
        data = yaml.safe_load(yaml_str)
    except Exception:
        data = None
    if data is None:
        return (yaml_str or "").lower()

    parts: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif node is not None:
            parts.append(str(node))

    walk(data)
    return " ".join(parts).lower()


# ── Workflow Nodes ───────────────────────────────────────────────────────────

async def node_ingest(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 1: Benchmark baseline ATS score and configure iteration limits."""
    selected_template = state.get("selected_template", "jakes_resume")
    target_page_count = 1 if selected_template == "compact_onepage" else 2
    max_retries = state.get("max_retries", 2)

    original_ats_score = state.get("original_ats_score", 0.0)
    parsed_jd_obj = _normalize_parsed_jd(state.get("parsed_jd"))

    # Compute baseline ATS if not already provided or if 0
    if not original_ats_score or original_ats_score <= 0.0:
        if state.get("llm") and state.get("original_resume_yaml"):
            try:
                base_ats = await ats_detailed(state["original_resume_yaml"], parsed_jd_obj, state["llm"])
                original_ats_score = float(base_ats.overall_score)
            except Exception as e:
                logger.warning(f"[node_ingest] Baseline ATS calculation failed: {e}. Defaulting to 50.0")
                original_ats_score = 50.0
        else:
            original_ats_score = 50.0

    return {
        "iteration_count": 0,
        "max_retries": max_retries,
        "score_history": [],
        "critique_notes": [],
        "target_page_count": target_page_count,
        "original_ats_score": original_ats_score,
        "current_resume_yaml": state.get("original_resume_yaml", ""),
    }


async def node_draft(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 2: Generate or revise candidate YAML using layout guidance & critique notes."""
    # Iteration count corresponds to the number of completed attempts
    current_iter = len(state.get("score_history", []))
    selected_template = state.get("selected_template", "jakes_resume")
    guidance = TEMPLATE_GUIDANCE.get(selected_template, TEMPLATE_GUIDANCE.get("jakes_resume", ""))

    # Critique notes only describe the previous attempt; they are consumed here
    critique_notes = state.get("critique_notes", [])
    if current_iter > 0 and critique_notes:
        critique_block = (
            "\n\nCRITICAL REVISIONS REQUIRED FROM PREVIOUS DRAFT:\n"
            + "\n".join(f"- {note}" for note in critique_notes)
        )
        guidance = guidance + critique_block

    parsed_jd_obj = _normalize_parsed_jd(state.get("parsed_jd"))
    skills_str = ", ".join(parsed_jd_obj.skills) if isinstance(parsed_jd_obj.skills, list) else str(parsed_jd_obj.skills)

    llm = state.get("llm")
    if not llm:
        raise ValueError("LLM instance is required in state for node_draft.")

    # Retry from the best candidate so far (not the latest, possibly regressed, draft)
    best = _best_candidate(state)
    base_yaml = best["yaml"] if best else (state.get("current_resume_yaml") or state["original_resume_yaml"])

    chain = build_optimization_chain(llm)
    response = await chain.ainvoke(
        input={
            "job_title": parsed_jd_obj.job_title,
            "skills": skills_str,
            "job_description": parsed_jd_obj.job_description,
            "resume_yaml": base_yaml,
            "addons": state.get("addons") or "No additional information provided.",
            "template_guidance": guidance,
        }
    )

    optimized_yaml = response.content if hasattr(response, "content") else str(response)

    # Strip markdown code fences
    if "```" in optimized_yaml:
        optimized_yaml = (
            optimized_yaml.split("```yaml")[-1]
            if "```yaml" in optimized_yaml
            else optimized_yaml.split("```")[-1]
        )
        optimized_yaml = optimized_yaml.split("```")[0].strip()

    return {
        "current_resume_yaml": optimized_yaml,
        "iteration_count": current_iter,
        "critique_notes": [],
    }


def node_factual_guardrails(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 3: Deterministic factual protection (0 LLM tokens).
    Overwrites education verbatim from original and protects contact information.
    """
    orig_yaml_str = state.get("original_resume_yaml", "")
    curr_yaml_str = state.get("current_resume_yaml", "")

    try:
        orig_data = yaml.safe_load(orig_yaml_str) or {}
        curr_data = yaml.safe_load(curr_yaml_str) or {}

        if isinstance(orig_data, dict) and isinstance(curr_data, dict):
            # 1. Restore education section verbatim
            if "education" in orig_data:
                curr_data["education"] = copy.deepcopy(orig_data["education"])

            # 2. Verify and restore contact details
            orig_contact = orig_data.get("contact")
            if isinstance(orig_contact, dict):
                if "contact" not in curr_data or not isinstance(curr_data["contact"], dict):
                    curr_data["contact"] = {}
                for field in ["phone", "email", "linkedin", "github", "location"]:
                    if field in orig_contact:
                        curr_data["contact"][field] = orig_contact[field]

            cleaned_yaml = yaml.dump(
                curr_data,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        else:
            cleaned_yaml = curr_yaml_str
    except Exception as e:
        logger.warning(f"[node_factual_guardrails] Guardrail processing warning: {e}")
        cleaned_yaml = curr_yaml_str

    return {
        "current_resume_yaml": cleaned_yaml,
        "guardrails_passed": True,
    }


def node_page_budget(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 4: In-memory Tectonic compilation & PDF page budget validation (all templates)."""
    selected_template = state.get("selected_template", "jakes_resume")
    target_pages = _target_pages(state)
    curr_yaml = state.get("current_resume_yaml", "")
    critique_notes = list(state.get("critique_notes", []))

    rendered_latex = None
    pdf_bytes = None
    actual_page_count: Optional[int] = None
    compile_error: Optional[str] = None

    try:
        rendered_latex = render_latex_template(curr_yaml, template_name=selected_template)
        pdf_bytes = compile_latex_to_pdf(rendered_latex)
        reader = PdfReader(io.BytesIO(pdf_bytes))
        actual_page_count = len(reader.pages)
    except Exception as e:
        # Page count is unknown: record it instead of guessing, and don't penalise the draft for it
        logger.warning(f"[node_page_budget] LaTeX compilation / PDF reading error: {e}")
        compile_error = type(e).__name__

    if actual_page_count is not None and actual_page_count > target_pages:
        page_budget_passed = False
        if target_pages == 1:
            hint = "shorten bullets, trim secondary duties, and guarantee strictly 1-page fit"
        else:
            hint = f"tighten wording and drop the weakest bullets so it fits within {target_pages} pages"
        critique_notes.append(
            f"Compiled PDF was {actual_page_count} pages (budget: {target_pages}). For {selected_template}, {hint}."
        )
    else:
        page_budget_passed = True

    return {
        "rendered_latex": rendered_latex,
        "compiled_pdf_bytes": pdf_bytes,
        "actual_page_count": actual_page_count,
        "compile_error": compile_error,
        "page_budget_passed": page_budget_passed,
        "critique_notes": critique_notes,
    }


async def node_ats_evaluator(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 5: ATS scoring & benchmark regression detection."""
    parsed_jd_obj = _normalize_parsed_jd(state.get("parsed_jd"))
    curr_yaml = state.get("current_resume_yaml", "")
    llm = state.get("llm")
    baseline_score = state.get("original_ats_score", 0.0)
    critique_notes = list(state.get("critique_notes", []))

    if llm:
        ats_result = await ats_detailed(curr_yaml, parsed_jd_obj, llm)
        score = float(ats_result.overall_score)
        details = ats_result.model_dump()
        missing_kw = (
            ats_result.keyword_analysis.missing_critical_keywords
            if hasattr(ats_result, "keyword_analysis")
            else []
        )
    else:
        score = baseline_score
        details = {}
        missing_kw = []

    # Score regression check
    if score >= baseline_score:
        ats_score_passed = True
    else:
        ats_score_passed = False
        critique_notes.append(
            f"ATS score regressed ({score:.1f} vs baseline {baseline_score:.1f}). "
            f"Missing critical keywords: {missing_kw}."
        )

    score_history = list(state.get("score_history", []))
    score_history.append({
        "iteration": state.get("iteration_count", 0),
        "score": score,
        "yaml": curr_yaml,
        "latex": state.get("rendered_latex"),
        "page_count": state.get("actual_page_count"),
        "details": details,
    })

    return {
        "current_ats_score": score,
        "current_ats_details": details,
        "ats_score_passed": ats_score_passed,
        "score_history": score_history,
        "critique_notes": critique_notes,
    }


def node_finalize(state: ResumeOptimizationState) -> Dict[str, Any]:
    """Node 6: Candidate selection, diff computation, and final artifact generation."""
    score_history = state.get("score_history", [])
    selected_template = state.get("selected_template", "jakes_resume")

    # If all criteria passed on current run
    if state.get("page_budget_passed", True) and state.get("ats_score_passed", True):
        final_yaml = state.get("current_resume_yaml", state.get("original_resume_yaml", ""))
        final_latex = state.get("rendered_latex")
        final_score = state.get("current_ats_score", state.get("original_ats_score", 0.0))
        is_satisfactory = True
        actual_page_count = state.get("actual_page_count")
        final_details = state.get("current_ats_details")
    else:
        # Best-effort candidate selector from score_history
        is_satisfactory = False
        best = _best_candidate(state)
        if best:
            final_yaml = best["yaml"]
            final_latex = best.get("latex")
            final_score = best.get("score", state.get("current_ats_score", 0.0))
            actual_page_count = best.get("page_count")
            final_details = best.get("details")
        else:
            final_yaml = state.get("current_resume_yaml", state.get("original_resume_yaml", ""))
            final_latex = state.get("rendered_latex")
            final_score = state.get("current_ats_score", state.get("original_ats_score", 0.0))
            actual_page_count = state.get("actual_page_count")
            final_details = state.get("current_ats_details")

    # Render latex if not already available
    if not final_latex:
        try:
            final_latex = render_latex_template(final_yaml, template_name=selected_template)
        except Exception as e:
            logger.warning(f"[node_finalize] Final LaTeX rendering fallback: {e}")
            final_latex = ""

    # Keywords actually added: JD keywords present in the final resume but absent from the original
    jd_obj = _normalize_parsed_jd(state.get("parsed_jd"))
    jd_skills = jd_obj.skills if isinstance(jd_obj.skills, list) else [str(jd_obj.skills)]
    candidates: List[str] = []
    seen = set()
    matched_final: List[str] = []
    if final_details and "keyword_analysis" in final_details:
        matched_final = final_details["keyword_analysis"].get("matched_keywords", []) or []
    for kw in list(jd_skills) + list(matched_final):
        kw = str(kw).strip()
        if kw and kw.lower() not in seen:
            seen.add(kw.lower())
            candidates.append(kw)

    orig_text = _flatten_text(state.get("original_resume_yaml", ""))
    final_text = _flatten_text(final_yaml)
    keywords_added = [kw for kw in candidates if kw.lower() in final_text and kw.lower() not in orig_text]

    original_score = state.get("original_ats_score", 0.0)
    score_delta = round(final_score - original_score, 2)

    improvements_made = [
        f"ATS score moved from {original_score:.1f} to {final_score:.1f} (delta: {score_delta:+.1f})",
    ]
    if keywords_added:
        improvements_made.append(f"Added {len(keywords_added)} job-description keywords: {', '.join(keywords_added[:10])}")
    target_pages = _target_pages(state)
    if actual_page_count is None:
        improvements_made.append("Page count could not be verified (PDF compile unavailable)")
    elif actual_page_count <= target_pages:
        improvements_made.append(f"Page budget met ({actual_page_count} of {target_pages} pages max)")
    else:
        improvements_made.append(f"Page budget exceeded ({actual_page_count} pages; target {target_pages}) - best effort result")

    return {
        "final_yaml": final_yaml,
        "final_latex": final_latex,
        "current_ats_score": final_score,
        "actual_page_count": actual_page_count,
        "keywords_added": keywords_added,
        "improvements_made": improvements_made,
        "is_satisfactory": is_satisfactory,
    }


# ── Router & StateGraph Construction ─────────────────────────────────────────

def should_continue(state: ResumeOptimizationState) -> str:
    """Conditional Edge Router:
    - If page budget AND ATS benchmark pass -> finalize
    - If failures exist and retries remain -> loop back to draft
    - Retries exhausted -> fallback to finalize
    """
    if state.get("page_budget_passed", True) and state.get("ats_score_passed", True):
        return "finalize"

    if state.get("iteration_count", 0) < state.get("max_retries", 2):
        return "draft"

    return "finalize"


def build_resume_graph() -> Any:
    """Constructs and compiles the LangGraph optimization workflow."""
    workflow = StateGraph(ResumeOptimizationState)

    workflow.add_node("node_ingest", node_ingest)
    workflow.add_node("node_draft", node_draft)
    workflow.add_node("node_factual_guardrails", node_factual_guardrails)
    workflow.add_node("node_page_budget", node_page_budget)
    workflow.add_node("node_ats_evaluator", node_ats_evaluator)
    workflow.add_node("node_finalize", node_finalize)

    workflow.add_edge(START, "node_ingest")
    workflow.add_edge("node_ingest", "node_draft")
    workflow.add_edge("node_draft", "node_factual_guardrails")
    workflow.add_edge("node_factual_guardrails", "node_page_budget")
    workflow.add_edge("node_page_budget", "node_ats_evaluator")

    workflow.add_conditional_edges(
        "node_ats_evaluator",
        should_continue,
        {
            "draft": "node_draft",
            "finalize": "node_finalize",
        },
    )
    workflow.add_edge("node_finalize", END)

    return workflow.compile()


resume_graph = build_resume_graph()


async def run_resume_optimization_workflow(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the multi-node optimization and validation workflow asynchronously."""
    return await resume_graph.ainvoke(inputs)
