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
             patch.object(main, "reserve_generation_slot", return_value=(main.get_week_start(), 1)), \
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


class TestFailedGenerationsAreNotCounted(Base):
    """Server AI takes a quota slot up front and gives it back if the generation fails."""

    GOOD = {
        "final_yaml": "name: Jane\nskills:\n  - Python\n",
        "final_latex": "tex",
        "current_ats_score": 82.0,
        "keywords_added": [],
        "improvements_made": [],
        "actual_page_count": 1,
        "score_history": [{}],
        "current_ats_details": {"match_level": "Good"},
    }

    def _optimize(self, workflow, *, byok=False, reserve=None, daily=None):
        jd = parsedJobDescription(job_title="Eng", skills=["Python"], job_description="d")
        reserve = reserve or MagicMock(return_value=(main.get_week_start(), 1))
        release = MagicMock()
        body = {"job_desc": JD, "original_ats_score": 60}
        if byok:
            self.user.is_byok_allowed = True
            body["ai_config"] = {"provider": "openai", "api_key": "sk-testtesttest"}
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "get_or_parse_jd", AsyncMock(return_value=(jd, "id"))), \
             patch.object(main, "optimize_resume_agentic", workflow), \
             patch.object(main, "reserve_generation_slot", reserve), \
             patch.object(main, "release_generation_slot", release), \
             patch.object(main, "get_weekly_usage", return_value=1), \
             patch.object(main, "get_daily_usage", daily or MagicMock(return_value=1)), \
             patch.object(main, "get_monthly_usage", return_value=1):
            res = self.client.post("/optimize-resume", json=body)
        return res, reserve, release

    def test_success_takes_one_slot_and_keeps_it(self):
        res, reserve, release = self._optimize(AsyncMock(return_value=self.GOOD))
        self.assertEqual(res.status_code, 200, res.text)
        reserve.assert_called_once()
        release.assert_not_called()
        self.db.commit.assert_called_once()
        self.assertEqual(res.json()["weekly_usage"], 1)

    def test_workflow_exception_gives_the_slot_back(self):
        res, reserve, release = self._optimize(AsyncMock(side_effect=Exception("LLM exploded")))
        self.assertGreaterEqual(res.status_code, 400)
        release.assert_called_once()
        self.db.add.assert_not_called()

    def test_empty_result_gives_the_slot_back(self):
        res, _, release = self._optimize(AsyncMock(return_value={**self.GOOD, "final_yaml": "   "}))
        self.assertEqual(res.status_code, 502)
        self.assertIn("not counted", res.json()["detail"])
        release.assert_called_once()
        self.db.add.assert_not_called()

    def test_unparseable_result_gives_the_slot_back(self):
        res, _, release = self._optimize(AsyncMock(return_value={**self.GOOD, "final_yaml": "just a sentence"}))
        self.assertEqual(res.status_code, 502)
        release.assert_called_once()

    def test_non_numeric_score_gives_the_slot_back(self):
        res, _, release = self._optimize(AsyncMock(return_value={**self.GOOD, "current_ats_score": None}))
        self.assertGreaterEqual(res.status_code, 400)
        release.assert_called_once()

    def test_commit_failure_rolls_back_and_gives_the_slot_back(self):
        self.db.commit.side_effect = Exception("db down")
        res, _, release = self._optimize(AsyncMock(return_value=self.GOOD))
        self.assertEqual(res.status_code, 500)
        self.db.rollback.assert_called_once()
        release.assert_called_once()

    def test_full_week_is_429_and_nothing_runs(self):
        workflow = AsyncMock(return_value=self.GOOD)
        full = MagicMock(side_effect=main.HTTPException(status_code=429, detail="Weekly generation limit reached (5/5)."))
        res, _, release = self._optimize(workflow, reserve=full)
        self.assertEqual(res.status_code, 429)
        workflow.assert_not_called()
        release.assert_not_called()

    def test_byok_generation_never_touches_the_quota(self):
        res, reserve, release = self._optimize(AsyncMock(return_value=self.GOOD), byok=True)
        self.assertEqual(res.status_code, 200, res.text)
        reserve.assert_not_called()
        release.assert_not_called()

    def test_meter_failure_after_commit_keeps_the_slot(self):
        res, _, release = self._optimize(AsyncMock(return_value=self.GOOD),
                                         daily=MagicMock(side_effect=Exception("meter down")))
        self.assertEqual(res.status_code, 200, res.text)
        release.assert_not_called()


class TestOneGenerationAtATime(Base):
    def test_second_request_while_one_runs_is_409(self):
        main._generations_in_flight.add(str(self.user.id))
        try:
            with patch.object(main, "reserve_generation_slot") as reserve:
                res = self.client.post("/optimize-resume", json={"job_desc": JD, "original_ats_score": 60})
            self.assertEqual(res.status_code, 409)
            reserve.assert_not_called()
        finally:
            main._generations_in_flight.discard(str(self.user.id))

    def test_guard_is_released_after_a_failure(self):
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "reserve_generation_slot",
                          side_effect=main.HTTPException(status_code=429, detail="full")):
            self.client.post("/optimize-resume", json={"job_desc": JD, "original_ats_score": 60})
        self.assertNotIn(str(self.user.id), main._generations_in_flight)


class TestReserveGenerationSlot(unittest.TestCase):
    """The reservation is one conditional upsert, so it can't overshoot the limit."""

    def test_statement_is_a_conditional_upsert(self):
        from sqlalchemy.dialects import postgresql
        db = MagicMock()
        db.execute.return_value.scalar.return_value = 3
        week, count = main.reserve_generation_slot(make_user(), db)
        self.assertEqual(count, 3)
        sql = str(db.execute.call_args[0][0].compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_generation_usage_user_week DO UPDATE", sql)
        self.assertIn("WHERE generation_usage.count <", sql)
        db.commit.assert_called_once()

    def test_full_week_raises_429(self):
        db = MagicMock()
        db.execute.return_value.scalar.return_value = None  # no row updated: already at the limit
        with self.assertRaises(main.HTTPException) as ctx:
            main.reserve_generation_slot(make_user(), db)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_release_never_raises(self):
        db = MagicMock()
        db.commit.side_effect = Exception("db down")
        main.release_generation_slot(uuid.uuid4(), main.get_week_start(), db)  # logged, not raised


class TestGoogleAuth(Base):
    def _google(self, tokeninfo, userinfo, status=200):
        class Resp:
            def __init__(self, code, body):
                self.status_code, self._body = code, body

            def json(self):
                return self._body

        async def fake_get(url, **kw):
            if "tokeninfo" in url:
                return Resp(status, tokeninfo)
            return Resp(200, userinfo)

        client = MagicMock()
        client.get = fake_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client), \
             patch.dict(os.environ, {"GOOGLE_CLIENT_IDS": "ours.apps.googleusercontent.com"}):
            return self.client.post("/auth/google", json={"access_token": "ya29.test-token"})

    def test_token_for_another_app_is_rejected(self):
        res = self._google({"aud": "someone-else.apps.googleusercontent.com", "email": "a@b.com"},
                           {"email": "a@b.com", "verified_email": True})
        self.assertEqual(res.status_code, 401)
        self.assertIn("not issued for Sculpt", res.json()["detail"])

    def test_invalid_token_is_rejected(self):
        res = self._google({"error": "invalid_token"}, {}, status=400)
        self.assertEqual(res.status_code, 401)

    def test_unverified_google_email_is_rejected(self):
        res = self._google({"aud": "ours.apps.googleusercontent.com", "email": "a@b.com"},
                           {"email": "a@b.com", "verified_email": False})
        self.assertEqual(res.status_code, 403)

    def test_our_token_signs_in(self):
        existing = make_user(email="a@b.com")
        self.db.query.return_value.filter.return_value.first.return_value = existing
        with patch.object(main, "build_auth_response", return_value={
            "access_token": "jwt", "token_type": "bearer", "user_id": str(existing.id),
            "email": "a@b.com", "full_name": "A", "plan": "free", "has_resume": True,
            "weekly_usage": 0, "weekly_limit": 5,
        }):
            res = self._google({"aud": "ours.apps.googleusercontent.com", "email": "A@b.com"},
                               {"email": "a@b.com", "verified_email": True, "name": "A"})
        self.assertEqual(res.status_code, 200, res.text)


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


class TestDownloadFilenames(Base):
    """Downloads are named after the person on the resume: Jane_Doe_resume.pdf."""

    YAML = 'name: "Jane Doe"\nskills: [Python]\n'

    def test_pdf_named_after_resume(self):
        with patch.object(main, "generate_pdf_from_resume", return_value=b"%PDF-1.4"):
            res = self.client.post("/generate-pdf", json={"resume_yaml": self.YAML},
                                   headers={"Origin": "http://localhost:5173"})
        self.assertEqual(res.status_code, 200)
        self.assertIn('filename="Jane_Doe_resume.pdf"', res.headers["content-disposition"])
        # the dashboard runs on another origin and can only read the name if it is exposed
        self.assertEqual(res.headers.get("access-control-expose-headers"), "Content-Disposition")

    def test_tex_named_after_resume(self):
        with patch.object(main, "render_latex_template", return_value="\documentclass{article}"):
            res = self.client.post("/generate-pdf", json={"resume_yaml": self.YAML, "return_tex": True})
        self.assertIn('filename="Jane_Doe_resume.tex"', res.headers["content-disposition"])

    def test_history_redownload_named_after_resume(self):
        record = MagicMock(optimized_yaml=self.YAML, selected_template="jakes_resume", job_title="Eng")
        self.db.query.return_value.filter.return_value.first.return_value = record
        with patch.object(main, "generate_pdf_from_resume", return_value=b"%PDF-1.4"):
            res = self.client.get(f"/my-optimizations/{uuid.uuid4()}/pdf")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn('filename="Jane_Doe_resume.pdf"', res.headers["content-disposition"])

    def test_name_rules(self):
        f = main.resume_download_filename
        self.assertEqual(f('name: "  María  José "'), "María_José_resume.pdf")
        self.assertEqual(f('name: "../../etc/passwd"'), "etcpasswd_resume.pdf")
        self.assertEqual(f("skills: [x]", make_user(full_name="Udit Bhatia")), "Udit_Bhatia_resume.pdf")
        self.assertEqual(f("skills: [x]", make_user(full_name=None), "tex"), "resume.tex")
        self.assertIn('filename="Maria_Jose_resume.pdf"', main.attachment_header("María_José_resume.pdf"))
