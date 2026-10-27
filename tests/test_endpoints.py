"""
test_endpoints.py
=================
Endpoint-level tests with the database, LLM and PDF compiler mocked out:
upload (PDF/DOCX validation + caps), ATS, optimize and the provider-error mapping.
"""

import io
import os
import sys
import unittest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient

import main
from main import app, get_current_user, get_db
from schema.schema import parsedJobDescription

JD = "We need a Python backend engineer with FastAPI, PostgreSQL and Docker experience. " * 2
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_user(**over):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "user@example.com"
    u.plan = "free"
    u.resume_yaml = "name: Jane\nskills:\n  - Python\n"
    u.email_verified = True
    u.is_byok_allowed = False
    u.is_admin = False
    u.selected_template = "jakes_resume"
    for k, v in over.items():
        setattr(u, k, v)
    return u


def docx_bytes(text="Jane Doe\nPython developer"):
    from docx import Document
    d = Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.db = MagicMock()
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()


class TestUpload(Base):
    def _post(self, name, data, ctype):
        return self.client.post("/upload-resume", files={"file": (name, data, ctype)})

    def test_unverified_blocked(self):
        self.user.email_verified = False
        self.assertEqual(self._post("r.pdf", b"%PDF-1.4", "application/pdf").status_code, 403)

    def test_rejects_other_types(self):
        self.assertEqual(self._post("r.txt", b"hello", "text/plain").status_code, 400)

    def test_rejects_fake_pdf(self):
        self.assertEqual(self._post("r.pdf", b"not a pdf at all", "application/pdf").status_code, 400)

    def test_rejects_fake_docx(self):
        self.assertEqual(self._post("r.docx", b"not a zip", DOCX_MIME).status_code, 400)

    def test_rejects_too_long_resume(self):
        big = docx_bytes("word " * (main.MAX_RESUME_CHARS // 4))
        res = self._post("r.docx", big, DOCX_MIME)
        self.assertEqual(res.status_code, 400)
        self.assertIn("too long", res.json()["detail"])

    def test_docx_upload_stores_parsed_yaml(self):
        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value=MagicMock(content="```yaml\nname: Jane\n```"))
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "build_res2yaml_chain", return_value=chain):
            res = self._post("resume.docx", docx_bytes(), DOCX_MIME)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.user.resume_yaml, "name: Jane")
        self.assertEqual(self.user.resume_filename, "resume.docx")


class TestAts(Base):
    BODY = {"job_desc": JD}

    def test_unverified_blocked(self):
        self.user.email_verified = False
        self.assertEqual(self.client.post("/calculate-ats-detailed", json=self.BODY).status_code, 403)

    def test_no_resume_404(self):
        self.user.resume_yaml = None
        self.assertEqual(self.client.post("/calculate-ats-detailed", json=self.BODY).status_code, 404)

    def test_bad_cache_id_is_422_not_500(self):
        res = self.client.post("/calculate-ats-detailed", json={**self.BODY, "jd_cache_id": "nope"})
        self.assertEqual(res.status_code, 422)

    def _run_with_provider_error(self, message):
        jd = parsedJobDescription(job_title="Eng", skills=["Python"], job_description="d")
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "get_or_parse_jd", AsyncMock(return_value=(jd, "id"))), \
             patch.object(main, "ats_detailed", AsyncMock(side_effect=Exception(message))):
            return self.client.post("/calculate-ats-detailed", json=self.BODY)

    def test_model_not_found_maps_to_400(self):
        res = self._run_with_provider_error("Error code: 404 - model_not_found llama-3.3")
        self.assertEqual(res.status_code, 400)
        self.assertIn("model", res.json()["detail"].lower())
        self.assertNotIn("llama", res.json()["detail"])  # raw provider text is not echoed

    def test_rate_limit_maps_to_429(self):
        self.assertEqual(self._run_with_provider_error("429 rate limit exceeded").status_code, 429)

    def test_unknown_error_is_generic_500(self):
        res = self._run_with_provider_error("boom: secret internals")
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("secret", res.json()["detail"])


class TestOptimize(Base):
    def test_success_persists_template_and_returns_changes(self):
        jd = parsedJobDescription(job_title="Eng", skills=["Python"], job_description="d")
        workflow_result = {
            "final_yaml": "name: Jane\nskills:\n  - Python\n  - Docker\n",
            "final_latex": "tex",
            "current_ats_score": 82.0,
            "keywords_added": ["Docker"],
            "improvements_made": ["x"],
            "actual_page_count": 1,
            "score_history": [{}],
            "current_ats_details": {"match_level": "Good"},
        }
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "get_or_parse_jd", AsyncMock(return_value=(jd, "id"))), \
             patch.object(main, "optimize_resume_agentic", AsyncMock(return_value=workflow_result)), \
             patch.object(main, "increment_weekly_usage", return_value=1), \
             patch.object(main, "get_weekly_usage", return_value=1), \
             patch.object(main, "get_daily_usage", return_value=1), \
             patch.object(main, "get_monthly_usage", return_value=1):
            res = self.client.post("/optimize-resume", json={
                "job_desc": JD, "original_ats_score": 60, "template": "classic_ivy",
            })
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["optimized_score"], 82.0)
        self.assertEqual(body["selected_template"], "classic_ivy")
        self.assertEqual(self.user.selected_template, "classic_ivy")  # remembered for next time
        self.assertEqual(body["keywords_added"], ["Docker"])

    def test_invalid_template_rejected(self):
        res = self.client.post("/optimize-resume", json={"job_desc": JD, "template": "../../x"})
        self.assertEqual(res.status_code, 422)


class TestAdminCleanupToken(Base):
    def test_requires_header_token(self):
        with patch.dict(os.environ, {"ADMIN_CLEANUP_TOKEN": "s3cret"}):
            self.assertEqual(self.client.delete("/admin/cleanup").status_code, 403)
            self.assertEqual(self.client.delete("/admin/cleanup?token=s3cret").status_code, 403)
            self.assertEqual(
                self.client.delete("/admin/cleanup", headers={"X-Cleanup-Token": "s3cret"}).status_code, 200)


class TestLlmErrorMapping(unittest.TestCase):
    def test_mapping(self):
        f = main.llm_error_to_http
        self.assertEqual(f(Exception("invalid_api_key"), "x").status_code, 401)
        self.assertEqual(f(Exception("model has been decommissioned"), "x").status_code, 400)
        self.assertEqual(f(Exception("insufficient_quota"), "x").status_code, 429)
        self.assertEqual(f(Exception("weird"), "fallback").detail, "fallback")


if __name__ == "__main__":
    unittest.main()
