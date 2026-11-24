"""
test_ai_allowance.py
====================
The daily allowance on server-paid AI calls (config/ai_allowance.py) and the endpoints it
guards: /upload-resume, /parse-jd, /explain-changes and /profile/refine. Database and LLM mocked.
"""

import asyncio
import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import profile_routes as pr
from config import ai_allowance as aa
from config.rate_limit import reset_rate_limits
from main import app, get_current_user, get_db
from schema.schema import MAX_RESUME_YAML_CHARS, parsedJobDescription
from tests.test_endpoints import DOCX_MIME, JD, docx_bytes, make_user

PARSED = parsedJobDescription(job_title="Eng", skills=["Python"], job_description="d")


def db_returning(count):
    """A mocked session whose upsert returns `count` (None = the row is already at the limit)."""
    db = MagicMock()
    db.execute.return_value.scalar.return_value = count
    return db


class TestReserve(unittest.TestCase):
    def test_takes_one_call(self):
        db = db_returning(1)
        self.assertEqual(aa.reserve_ai_call(make_user(), "upload", db), aa.utc_today())
        db.commit.assert_called_once()

    def test_full_day_is_429_with_the_limit_and_an_upgrade_hint(self):
        with self.assertRaises(HTTPException) as ctx:
            aa.reserve_ai_call(make_user(plan="free"), "explain", db_returning(None))
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("limit of 10 change explanations", ctx.exception.detail)
        self.assertIn("Pro allows 40", ctx.exception.detail)

    def test_pro_gets_its_own_limit_and_no_upgrade_hint(self):
        with self.assertRaises(HTTPException) as ctx:
            aa.reserve_ai_call(make_user(plan="pro"), "explain", db_returning(None))
        self.assertIn("limit of 40", ctx.exception.detail)
        self.assertNotIn("Pro allows", ctx.exception.detail)

    def test_unknown_bucket_is_a_bug(self):
        with self.assertRaises(ValueError):
            aa.reserve_ai_call(make_user(), "nope", db_returning(1))


class TestContextManager(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_failed_call_gives_the_unit_back(self):
        async def go():
            async with aa.ai_allowance(make_user(), "refine", MagicMock()):
                raise RuntimeError("provider down")
        with patch.object(aa, "reserve_ai_call", return_value=aa.utc_today()), \
             patch.object(aa, "release_ai_call") as release:
            with self.assertRaises(RuntimeError):
                self._run(go())
        release.assert_called_once()

    def test_successful_call_keeps_the_unit(self):
        async def go():
            async with aa.ai_allowance(make_user(), "refine", MagicMock()):
                pass
        with patch.object(aa, "reserve_ai_call", return_value=aa.utc_today()) as reserve, \
             patch.object(aa, "release_ai_call") as release:
            self._run(go())
        reserve.assert_called_once()
        release.assert_not_called()

    def test_byok_counts_nothing(self):
        async def go():
            async with aa.ai_allowance(make_user(), "refine", MagicMock(), skip=True):
                pass
        with patch.object(aa, "reserve_ai_call") as reserve:
            self._run(go())
        reserve.assert_not_called()


class Base(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()
        self.user = make_user()
        self.db = MagicMock()
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()


def full_day(*_args, **_kwargs):
    raise HTTPException(status_code=429, detail="You've reached today's limit.")


class TestParseJd(Base):
    def _post(self, cached, byok=False, **reserve):
        body = {"job_desc": JD}
        if byok:
            self.user.is_byok_allowed = True
            body["ai_config"] = {"provider": "openai", "api_key": "sk-test-1234"}
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "_jd_cache_lookup", return_value=(PARSED, "cid") if cached else None), \
             patch.object(main, "get_or_parse_jd", AsyncMock(return_value=(PARSED, "cid"))) as parse, \
             patch.object(aa, "reserve_ai_call", **(reserve or {"return_value": aa.utc_today()})) as r:
            res = self.client.post("/parse-jd", json=body)
        return res, parse, r

    def test_cached_job_is_free(self):
        res, parse, reserve = self._post(cached=True)
        self.assertEqual(res.status_code, 200, res.text)
        reserve.assert_not_called()
        parse.assert_not_called()

    def test_new_job_counts(self):
        res, parse, reserve = self._post(cached=False)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(reserve.call_args.args[1], "jd_parse")
        parse.assert_awaited_once()

    def test_full_day_stops_before_the_llm(self):
        res, parse, _ = self._post(cached=False, side_effect=full_day)
        self.assertEqual(res.status_code, 429)
        parse.assert_not_called()

    def test_byok_is_not_counted(self):
        res, _, reserve = self._post(cached=False, byok=True)
        self.assertEqual(res.status_code, 200, res.text)
        reserve.assert_not_called()

    def test_unverified_email_is_refused(self):
        self.user.email_verified = False
        res, parse, _ = self._post(cached=False)
        self.assertEqual(res.status_code, 403)
        parse.assert_not_called()


class TestUpload(Base):
    def test_full_day_stops_before_the_llm_and_saves_nothing(self):
        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value=MagicMock(content="name: Jane"))
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "build_res2yaml_chain", return_value=chain), \
             patch.object(aa, "reserve_ai_call", side_effect=full_day):
            res = self.client.post("/upload-resume", files={"file": ("r.docx", docx_bytes(), DOCX_MIME)})
        self.assertEqual(res.status_code, 429)
        chain.ainvoke.assert_not_called()
        self.assertEqual(self.user.resume_yaml, "name: Jane\nskills:\n  - Python\n")

    def test_failed_parse_gives_the_unit_back(self):
        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "build_res2yaml_chain", return_value=chain), \
             patch.object(aa, "reserve_ai_call", return_value=aa.utc_today()), \
             patch.object(aa, "release_ai_call") as release:
            res = self.client.post("/upload-resume", files={"file": ("r.docx", docx_bytes(), DOCX_MIME)})
        self.assertEqual(res.status_code, 502)
        release.assert_called_once()


class TestExplain(Base):
    def setUp(self):
        super().setUp()
        # find_explained_record: query(...).filter(user).filter(yaml).order_by(...).first() -> no saved record
        self.db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.first.return_value = None

    def _post(self, body, **reserve):
        explain = AsyncMock(return_value={"summary_rationale": "x", "key_enhancements": [], "skills_strategy": []})
        with patch.object(main, "resolve_request_llm", return_value=object()), \
             patch.object(main, "explain_resume_changes", explain), \
             patch.object(aa, "reserve_ai_call", **(reserve or {"return_value": aa.utc_today()})) as r:
            res = self.client.post("/explain-changes", json=body)
        return res, explain, r

    def test_new_explanation_counts(self):
        res, explain, reserve = self._post({"optimized_yaml": "name: Jane\n"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(reserve.call_args.args[1], "explain")

    def test_full_day_is_429(self):
        res, explain, _ = self._post({"optimized_yaml": "name: Jane\n"}, side_effect=full_day)
        self.assertEqual(res.status_code, 429)
        explain.assert_not_called()

    def test_oversized_input_is_refused_before_any_ai_call(self):
        res, explain, _ = self._post({"optimized_yaml": "x" * (MAX_RESUME_YAML_CHARS + 1)})
        self.assertEqual(res.status_code, 422)
        explain.assert_not_called()

    def test_unverified_email_is_refused(self):
        self.user.email_verified = False
        res, explain, _ = self._post({"optimized_yaml": "name: Jane\n"})
        self.assertEqual(res.status_code, 403)
        explain.assert_not_called()


class TestRefine(Base):
    def test_full_day_is_429_not_a_polish_error(self):
        chain = MagicMock()
        chain.ainvoke = AsyncMock(return_value=SimpleNamespace(refined="Better"))
        with patch.object(pr, "build_server_llm", return_value=object()), \
             patch.object(pr, "build_refine_chain", return_value=chain), \
             patch.object(aa, "reserve_ai_call", side_effect=full_day):
            res = self.client.post("/profile/refine", json={"kind": "achievement", "text": "won a hackathon"})
        self.assertEqual(res.status_code, 429)
        chain.ainvoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
