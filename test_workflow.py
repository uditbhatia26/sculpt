"""
test_workflow.py
================
Automated test suite for Pillar 2 LangGraph Multi-Node Pipeline:
1. State Schema & Graph Compilation
2. Factual Guardrails (Education & Contact Immutability)
3. Page Budget Node (Tectonic Compilation & Page Counting)
4. Conditional Edge Routing & Max Retry Exhaustion
5. Best-Effort Final Candidate Selection
"""

import sys
import yaml
import asyncio
from typing import Dict, Any

from models.workflow import (
    ResumeOptimizationState,
    resume_graph,
    node_factual_guardrails,
    node_page_budget,
    node_finalize,
    should_continue,
    build_resume_graph,
)

SAMPLE_BASE_YAML = """name: "Udit Bhatia"
contact:
  phone: "+91-9717228929"
  email: "bhatiaudit.work@gmail.com"
  linkedin: "linkedin.com/in/uditbhatia"
  github: "github.com/uditbhatia"
  location: "Delhi, India"

education:
  - institution: "Guru Gobind Singh Indraprastha University"
    degree: "B.Tech in Computer Science and Engineering"
    cgpa: 8.4
    period: "2021 - 2025"

experience:
  - company: "Supervity"
    role: "Product Development Intern"
    period: "May 2025 - Sep 2025"
    location: "Remote"
    achievements:
      - "Built invoice automation platform with LLMs and OCR processing 10k documents/mo."
      - "Engineered FastAPI endpoints with Milvus vector search for RAG pipeline."

projects:
  - name: "Postify"
    description: "AI Ghostwriting Tool"
    stack: ["Python", "FastAPI", "OpenAI"]
    highlights:
      - "Generated 100+ viral posts with automated LinkedIn scheduling."

technical_skills:
  languages: ["Python", "JavaScript", "SQL"]
  frameworks: ["FastAPI", "React", "Docker"]
"""


def test_1_state_and_graph_compilation():
    """Test 1: Verify State structure and Graph compilation."""
    print("\n--- Running Test 1: State Schema & Graph Compilation ---")
    compiled = build_resume_graph()
    assert compiled is not None, "Workflow compilation failed"
    # Verify graph node names exist
    node_keys = compiled.nodes.keys()
    for expected_node in [
        "node_ingest",
        "node_draft",
        "node_factual_guardrails",
        "node_page_budget",
        "node_ats_evaluator",
        "node_finalize",
    ]:
        assert expected_node in node_keys, f"Missing node: {expected_node}"

    print("[PASS] Test 1: LangGraph compiled successfully with all 6 required nodes.")


def test_2_factual_guardrails():
    """Test 2: Ensure education and contact are 100% restored if altered by draft."""
    print("\n--- Running Test 2: Factual Guardrails (Education & Contact Immutability) ---")
    # Simulate LLM hallucinating / altering education & contact
    hallucinated_draft_yaml = """name: "Udit Bhatia"
contact:
  phone: "+1-555-0199"
  email: "fake_hacker@mit.edu"
  linkedin: "linkedin.com/in/fake"
  github: "github.com/fake"
  location: "Boston, MA"

education:
  - institution: "Massachusetts Institute of Technology"
    degree: "Ph.D. in Artificial Intelligence"
    cgpa: 4.0
    period: "2020 - 2024"

experience:
  - company: "Supervity"
    role: "Lead Architect"
    period: "May 2025 - Sep 2025"
    location: "Remote"
    achievements:
      - "Optimized backend throughput by 40%."
"""
    state: ResumeOptimizationState = {
        "original_resume_yaml": SAMPLE_BASE_YAML,
        "current_resume_yaml": hallucinated_draft_yaml,
    }

    result = node_factual_guardrails(state)
    assert result["guardrails_passed"] is True, "Guardrail flag not set"

    cleaned_data = yaml.safe_load(result["current_resume_yaml"])
    orig_data = yaml.safe_load(SAMPLE_BASE_YAML)

    # Verify education is 100% identical to original
    assert cleaned_data["education"] == orig_data["education"], (
        f"Education mismatch! Expected {orig_data['education']}, got {cleaned_data['education']}"
    )

    # Verify contact details are restored
    assert cleaned_data["contact"]["email"] == orig_data["contact"]["email"]
    assert cleaned_data["contact"]["phone"] == orig_data["contact"]["phone"]
    assert cleaned_data["contact"]["linkedin"] == orig_data["contact"]["linkedin"]

    print("[PASS] Test 2: Education and contact were strictly restored verbatim (0-token guardrail).")


def test_3_page_budget_node():
    """Test 3: In-memory Tectonic compilation & page budget enforcement for compact_onepage."""
    print("\n--- Running Test 3: Page Budget Node ---")

    # Case A: 1-Page fit with compact_onepage
    state_1page: ResumeOptimizationState = {
        "selected_template": "compact_onepage",
        "current_resume_yaml": SAMPLE_BASE_YAML,
        "critique_notes": [],
    }
    result_1page = node_page_budget(state_1page)
    print(f"  compact_onepage sample compiled to {result_1page['actual_page_count']} page(s).")
    assert result_1page["page_budget_passed"] is True, "Expected 1-page sample to pass page budget"
    assert result_1page["actual_page_count"] == 1, f"Expected 1 page, got {result_1page['actual_page_count']}"
    assert result_1page["compiled_pdf_bytes"] is not None
    assert len(result_1page["compiled_pdf_bytes"]) > 1000

    # Case B: Multi-page overflow with compact_onepage
    bloated_roles = []
    for i in range(1, 12):
        bloated_roles.append({
            "company": f"Tech Enterprise {i}",
            "role": f"Senior Software Specialist {i}",
            "period": f"201{i % 10} - 202{i % 5}",
            "location": "San Francisco, CA",
            "achievements": [
                f"Led engineering taskforce {i} delivering end-to-end distributed infrastructure.",
                f"Scaled microservice clusters processing billions of transaction events daily.",
                f"Mentored 15 junior developers and introduced modern CI/CD pipelines.",
            ]
        })
    bloated_data = yaml.safe_load(SAMPLE_BASE_YAML)
    bloated_data["experience"] = bloated_roles
    bloated_yaml = yaml.dump(bloated_data)

    state_bloated: ResumeOptimizationState = {
        "selected_template": "compact_onepage",
        "current_resume_yaml": bloated_yaml,
        "critique_notes": [],
    }
    result_bloated = node_page_budget(state_bloated)
    print(f"  Bloated resume compiled to {result_bloated['actual_page_count']} page(s).")
    assert result_bloated["actual_page_count"] > 1, "Expected bloated resume to exceed 1 page"
    assert result_bloated["page_budget_passed"] is False, "Expected multi-page compact_onepage to fail page budget"
    assert any("strictly 1-page fit" in note for note in result_bloated["critique_notes"]), "Critique note missing"

    print("[PASS] Test 3: Page budget node accurately detects 1-page fit vs overflow.")


def test_4_conditional_routing():
    """Test 4: Conditional routing (retry on failure, finalize on success or max retries)."""
    print("\n--- Running Test 4: Conditional Edge Routing ---")

    # Case A: Success on iteration 0 -> finalize
    state_pass: ResumeOptimizationState = {
        "page_budget_passed": True,
        "ats_score_passed": True,
        "iteration_count": 0,
        "max_retries": 2,
    }
    assert should_continue(state_pass) == "finalize", "Passed state should finalize"

    # Case B: Regressed score on iteration 0 -> retry (draft)
    state_regress_iter0: ResumeOptimizationState = {
        "page_budget_passed": True,
        "ats_score_passed": False,
        "iteration_count": 0,
        "max_retries": 2,
    }
    assert should_continue(state_regress_iter0) == "draft", "Failed state on iter 0 should retry"

    # Case C: Page overflow on iteration 1 -> retry (draft)
    state_overflow_iter1: ResumeOptimizationState = {
        "page_budget_passed": False,
        "ats_score_passed": True,
        "iteration_count": 1,
        "max_retries": 2,
    }
    assert should_continue(state_overflow_iter1) == "draft", "Failed state on iter 1 should retry"

    # Case D: Max retries exhausted (iteration 2 >= max_retries 2) -> finalize
    state_max_exhausted: ResumeOptimizationState = {
        "page_budget_passed": False,
        "ats_score_passed": False,
        "iteration_count": 2,
        "max_retries": 2,
    }
    assert should_continue(state_max_exhausted) == "finalize", "Exhausted retries must exit to finalize"

    print("[PASS] Test 4: Router correctly triggers retry loop on iteration 0/1 and terminates on iteration 2.")


def test_5_best_effort_finalize():
    """Test 5: Best-effort candidate selector picks 1-page fit with highest score."""
    print("\n--- Running Test 5: Best-Effort Finalize Selector ---")
    score_history = [
        {"iteration": 0, "score": 62.0, "yaml": "draft_0", "page_count": 1, "details": {}},
        {"iteration": 1, "score": 88.0, "yaml": "draft_1_overflow", "page_count": 2, "details": {}},
        {"iteration": 2, "score": 81.0, "yaml": "draft_2_onepage", "page_count": 1, "details": {}},
    ]
    state_compact: ResumeOptimizationState = {
        "selected_template": "compact_onepage",
        "page_budget_passed": False,
        "ats_score_passed": False,
        "score_history": score_history,
        "original_ats_score": 60.0,
    }
    result = node_finalize(state_compact)
    # For compact_onepage, candidate with page_count <= 1 and highest score should be picked (draft_2_onepage with 81.0)
    assert result["final_yaml"] == "draft_2_onepage", f"Expected draft_2_onepage, got {result['final_yaml']}"
    assert result["current_ats_score"] == 81.0
    assert result["actual_page_count"] == 1

    print("[PASS] Test 5: Finalizer selected highest-scoring 1-page candidate under budget failure.")


def run_all_tests():
    print("=" * 60)
    print("RUNNING PILLAR 2 LANGGRAPH PIPELINE VERIFICATION SUITE")
    print("=" * 60)

    test_1_state_and_graph_compilation()
    test_2_factual_guardrails()
    test_3_page_budget_node()
    test_4_conditional_routing()
    test_5_best_effort_finalize()

    print("\n" + "=" * 60)
    print("ALL 5 TESTS PASSED SUCCESSFULLY! PILLAR 2 VERIFIED.")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
