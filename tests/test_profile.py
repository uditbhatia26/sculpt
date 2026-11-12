"""
test_profile.py
===============
Profile pool, onboarding, GitHub connection and Chisel, with the database, GitHub and the
LLM mocked out.
"""

import asyncio
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient

import main
import profile_routes as pr
from config import github_client as gh
from config.rate_limit import reset_rate_limits
from main import app, get_current_user, get_db

NOW = datetime.now(timezone.utc)


def make_user(**over):
    u = SimpleNamespace(
        id=uuid.uuid4(), email="user@example.com", plan="free", email_verified=True,
        resume_yaml="name: Jane\n", resume_filename="jane.pdf", resume_uploaded_at=None,
        onboarding_step="projects", onboarding_completed_at=None, onboarding_grandfathered=False,
        github_id="42", github_username="jane", github_token_enc=gh.encrypt_token("gho_test"),
        github_private_access=False, chisel_generations_used=0,
        chisel_period_start=None, chisel_period_adds=0,
    )
    for k, v in over.items():
        setattr(u, k, v)
    return u


def make_project(**over):
    p = SimpleNamespace(
        id=uuid.uuid4(), source="github", name="Proj", summary="A thing", tech_stack=["Python"],
        bullets=["Built a", "Designed b", "Shipped c"], status="draft", repo_full_name="jane/proj",
        repo_url="https://github.com/jane/proj", repo_private=False, role="sole author", facts=None,
        repo_context="REPOSITORY: jane/proj", ai_edits_used=0,
    )
    for k, v in over.items():
        setattr(p, k, v)
    return p


GOOD = SimpleNamespace(project_name="Proj", summary="A thing", tech_stack=["Python", "FastAPI"],
                       bullets=["Built an API with FastAPI", "Designed a schema in PostgreSQL", "Shipped it with Docker"])
DIGEST = {"context": "REPOSITORY: jane/proj", "name": "proj", "url": "https://github.com/jane/proj",
          "private": False, "languages": ["Python"]}
CAN_ADD = {"can_add": True, "reason": "", "slots": 1, "used": 0, "plan": "free", "adds_per_period": 1,
           "adds_left": 1, "period_ends_at": None, "period_days": 30, "ai_edits_per_project": 3}


def chain_returning(result=None, error=None):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=result, side_effect=error)
    return MagicMock(return_value=chain)


class Base(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()
        pr._chisel_in_flight.clear()
        self.user = make_user()
        self.db = MagicMock()
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()


class TestOnboardingState(unittest.TestCase):
    def test_new_account_must_finish_setup(self):
        s = pr.onboarding_state(make_user(onboarding_step="resume"))
        self.assertEqual(s, {"onboarding_step": "resume", "onboarding_complete": False, "setup_required": True})

    def test_existing_account_is_not_locked_out(self):
        s = pr.onboarding_state(make_user(onboarding_grandfathered=True))
        self.assertFalse(s["setup_required"])
        self.assertFalse(s["onboarding_complete"])

    def test_finished(self):
        s = pr.onboarding_state(make_user(onboarding_completed_at=NOW))
        self.assertEqual(s, {"onboarding_step": "done", "onboarding_complete": True, "setup_required": False})


class TestOnboardingStep(Base):
    def test_cannot_pass_resume_step_without_a_resume(self):
        self.user.resume_yaml = None
        self.user.onboarding_step = "resume"
        res = self.client.post("/onboarding/step", json={"step": "projects"})
        self.assertEqual(res.status_code, 400)

    def test_moves_forward_and_finishes(self):
        self.assertEqual(self.client.post("/onboarding/step", json={"step": "achievements"}).json()["onboarding_step"], "achievements")
        body = self.client.post("/onboarding/step", json={"step": "done"}).json()
        self.assertTrue(body["onboarding_complete"])
        self.assertIsInstance(self.user.onboarding_completed_at, datetime)

    def test_never_moves_backward(self):
        self.user.onboarding_step = "achievements"
        self.client.post("/onboarding/step", json={"step": "projects"})
        self.assertEqual(self.user.onboarding_step, "achievements")

    def test_unknown_step_rejected(self):
        self.assertEqual(self.client.post("/onboarding/step", json={"step": "nope"}).status_code, 422)


class TestChiselAllowance(unittest.TestCase):
    """Slots saved at once, and adds per 30-day period starting with the first add."""

    def _status(self, user, saved):
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = saved
        return pr.chisel_status(user, db)

    def test_free_one_slot_one_add(self):
        s = self._status(make_user(), 0)
        self.assertEqual((s["slots"], s["adds_per_period"], s["adds_left"], s["can_add"]), (1, 1, 1, True))
        self.assertIsNone(s["period_ends_at"])  # no period running yet

    def test_pro_five_slots_five_adds(self):
        s = self._status(make_user(plan="pro"), 0)
        self.assertEqual((s["slots"], s["adds_per_period"], s["adds_left"]), (5, 5, 5))

    def test_used_adds_in_the_period_block_adding_and_name_the_refresh_date(self):
        start = NOW - timedelta(days=3)
        user = make_user(plan="pro", chisel_period_start=start, chisel_period_adds=5)
        s = self._status(user, 2)  # removed three, but the adds are spent
        self.assertFalse(s["can_add"])
        self.assertEqual(s["adds_left"], 0)
        ends = start + timedelta(days=30)
        self.assertIn(f"{ends.day} {ends.strftime('%b %Y')}", s["reason"])

    def test_full_slots_block_adding_even_with_adds_left(self):
        user = make_user(plan="pro", chisel_period_start=NOW - timedelta(days=40), chisel_period_adds=5)
        s = self._status(user, 5)
        self.assertEqual(s["adds_left"], 5)  # the old period ended, so adds are back
        self.assertFalse(s["can_add"])
        self.assertIn("Remove one", s["reason"])

    def test_removing_frees_a_slot_but_not_an_add(self):
        user = make_user(plan="pro", chisel_period_start=NOW - timedelta(days=1), chisel_period_adds=3)
        s = self._status(user, 2)
        self.assertEqual((s["adds_left"], s["can_add"]), (2, True))

    def test_period_ends_after_thirty_days(self):
        user = make_user(chisel_period_start=NOW - timedelta(days=30, minutes=1), chisel_period_adds=1)
        s = self._status(user, 0)
        self.assertEqual((s["adds_left"], s["can_add"]), (1, True))

    def test_counting_an_add_starts_and_continues_a_period(self):
        user = make_user(plan="pro")
        pr.count_chisel_add(user)
        first_start = user.chisel_period_start
        self.assertIsNotNone(first_start)
        self.assertEqual(user.chisel_period_adds, 1)
        pr.count_chisel_add(user)
        self.assertEqual((user.chisel_period_start, user.chisel_period_adds), (first_start, 2))
        self.assertEqual(user.chisel_generations_used, 2)

    def test_first_add_after_a_period_ends_starts_a_new_one(self):
        user = make_user(chisel_period_start=NOW - timedelta(days=31), chisel_period_adds=1)
        pr.count_chisel_add(user)
        self.assertEqual(user.chisel_period_adds, 1)
        self.assertGreater(user.chisel_period_start, NOW - timedelta(minutes=1))


class TestManualLimits(unittest.TestCase):
    def _status(self, user, saved):
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = saved
        return pr.manual_status(user, db)

    def test_free_one_at_a_time(self):
        self.assertEqual(self._status(make_user(), 0), {"limit": 1, "used": 0, "can_add": True})
        self.assertFalse(self._status(make_user(), 1)["can_add"])

    def test_pro_has_no_visible_limit_but_a_quiet_cap(self):
        self.assertEqual(self._status(make_user(plan="pro"), 19), {"limit": None, "used": 19, "can_add": True})
        self.assertFalse(self._status(make_user(plan="pro"), pr.MANUAL_PROJECT_SAFETY_CAP)["can_add"])


class TestChiselCreate(Base):
    BODY = {"repo_full_name": "jane/proj", "role": "I built all of it"}

    def _post(self, body=None, digest=DIGEST, digest_error=None, result=GOOD, llm_error=None, status=CAN_ADD):
        self.db.query.return_value.filter.return_value.first.return_value = None  # no duplicate
        client = MagicMock()
        client.repo_digest = AsyncMock(return_value=digest, side_effect=digest_error)
        with patch.object(pr.gh, "GitHub", return_value=client), \
             patch.object(pr, "build_server_llm", return_value=object()), \
             patch.object(pr, "build_chisel_chain", chain_returning(result, llm_error)), \
             patch.object(pr, "chisel_status", return_value=status):
            return self.client.post("/chisel/projects", json=body or self.BODY)

    def test_success_saves_a_draft_and_counts_one_add(self):
        res = self._post()
        self.assertEqual(res.status_code, 200, res.text)
        project = res.json()["project"]
        self.assertEqual(len(project["bullets"]), 3)
        self.assertEqual(project["status"], "draft")
        self.assertEqual(project["ai_edits_left"], 3)
        self.assertNotIn("locked", project)
        self.assertEqual(self.user.chisel_period_adds, 1)
        self.assertIsNotNone(self.user.chisel_period_start)
        self.db.add.assert_called_once()

    def test_limit_reached_is_refused_before_anything_runs(self):
        res = self._post(status={**CAN_ADD, "can_add": False, "reason": "You've used your free Chisel project."})
        self.assertEqual(res.status_code, 403)
        self.db.add.assert_not_called()

    def test_needs_github_connected(self):
        self.user.github_token_enc = None
        self.assertEqual(self._post().status_code, 409)

    def test_not_a_contributor_is_refused_and_not_counted(self):
        res = self._post(digest_error=gh.GitHubError("You don't appear as a contributor to this repository."))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.user.chisel_period_adds, 0)

    def test_dead_github_token_asks_to_reconnect_without_logging_out(self):
        res = self._post(digest_error=gh.GitHubError("expired", reconnect=True))
        self.assertEqual(res.status_code, 409)  # never 401: that would sign the user out of Sculpt
        self.assertIsNone(self.user.github_token_enc)

    def test_llm_failure_is_not_counted(self):
        res = self._post(llm_error=Exception("provider down"))
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.user.chisel_period_adds, 0)
        self.db.add.assert_not_called()

    def test_fewer_than_three_bullets_is_not_counted(self):
        res = self._post(result=SimpleNamespace(project_name="P", summary="", tech_stack=[], bullets=["only one"]))
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.user.chisel_period_adds, 0)

    def test_double_click_does_not_take_two_slots(self):
        pr._chisel_in_flight.add(str(self.user.id))
        self.assertEqual(self._post().status_code, 409)
        self.assertEqual(self.user.chisel_period_adds, 0)

    def test_unverified_email_blocked(self):
        self.user.email_verified = False
        self.assertEqual(self._post().status_code, 403)

    def test_bad_repo_name_rejected(self):
        self.assertEqual(self._post({**self.BODY, "repo_full_name": "not a repo"}).status_code, 422)


class TestChiselRevise(Base):
    def _revise(self, project, result=GOOD, llm_error=None):
        self.db.query.return_value.filter.return_value.first.return_value = project
        with patch.object(pr, "build_server_llm", return_value=object()), \
             patch.object(pr, "build_chisel_revise_chain", chain_returning(result, llm_error)):
            return self.client.post(f"/chisel/projects/{project.id}/revise", json={"instruction": "make them shorter"})

    def test_revision_uses_one_of_three_edits_and_needs_reapproval(self):
        project = make_project(status="approved", ai_edits_used=1)
        res = self._revise(project)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(project.ai_edits_used, 2)
        self.assertEqual(project.status, "draft")
        self.assertEqual(res.json()["project"]["ai_edits_left"], 1)

    def test_fourth_edit_is_refused(self):
        project = make_project(ai_edits_used=3)
        res = self._revise(project)
        self.assertEqual(res.status_code, 409)
        self.assertIn("edit the bullets yourself", res.json()["detail"])

    def test_failed_revision_is_not_counted(self):
        project = make_project(ai_edits_used=1)
        self.assertEqual(self._revise(project, llm_error=Exception("down")).status_code, 502)
        self.assertEqual(project.ai_edits_used, 1)

    def test_manual_projects_are_edited_by_hand(self):
        self.assertEqual(self._revise(make_project(source="manual")).status_code, 400)


class TestProjects(Base):
    def test_any_project_can_be_removed_right_after_adding(self):
        for project in (make_project(), make_project(source="manual")):
            self.db.reset_mock()
            self.db.query.return_value.filter.return_value.first.return_value = project
            with patch.object(pr, "chisel_status", return_value=CAN_ADD), \
                 patch.object(pr, "manual_status", return_value={"limit": 1, "used": 0, "can_add": True}):
                res = self.client.delete(f"/profile/projects/{project.id}")
            self.assertEqual(res.status_code, 200, res.text)
            self.db.delete.assert_called_once_with(project)
            self.assertIn("chisel", res.json())
            self.assertIn("manual", res.json())

    def test_typing_edits_is_unlimited_and_approving_works(self):
        project = make_project(ai_edits_used=3)
        self.db.query.return_value.filter.return_value.first.return_value = project
        res = self.client.patch(f"/profile/projects/{project.id}",
                                json={"bullets": ["- Rewrote it myself  ", "", "Second"], "status": "approved"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(project.bullets, ["Rewrote it myself", "Second"])
        self.assertEqual(project.status, "approved")
        self.assertEqual(project.ai_edits_used, 3)

    def test_someone_elses_project_is_not_found(self):
        self.db.query.return_value.filter.return_value.first.return_value = None
        self.assertEqual(self.client.delete(f"/profile/projects/{uuid.uuid4()}").status_code, 404)
        self.assertEqual(self.client.delete("/profile/projects/not-a-uuid").status_code, 404)

    def test_free_plan_second_hand_added_project_is_refused(self):
        self.db.query.return_value.filter.return_value.count.return_value = 1
        res = self.client.post("/profile/projects", json={"name": "Second", "bullets": ["Built another thing"]})
        self.assertEqual(res.status_code, 409)
        self.assertIn("Remove it to add a different one", res.json()["detail"])

    def test_manual_project_added_approved(self):
        self.db.query.return_value.filter.return_value.count.return_value = 0
        res = self.client.post("/profile/projects", json={"name": "Robot arm", "bullets": ["Built a 3-axis arm"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["project"]["status"], "approved")
        self.assertEqual(res.json()["project"]["source"], "manual")


class TestRefineAndAchievements(Base):
    def test_refine_reports_whether_it_changed_anything(self):
        with patch.object(pr, "build_server_llm", return_value=object()), \
             patch.object(pr, "build_refine_chain", chain_returning(SimpleNamespace(refined="Won 2nd place at HackX 2025"))):
            res = self.client.post("/profile/refine", json={"text": "i got 2nd place in hackx 2025"})
        self.assertEqual(res.json(), {"refined": "Won 2nd place at HackX 2025", "changed": True, "minor": False})

    def test_capitalisation_only_fix_is_minor(self):
        with patch.object(pr, "build_server_llm", return_value=object()),              patch.object(pr, "build_refine_chain", chain_returning(SimpleNamespace(refined="Participated in a hackathon."))):
            res = self.client.post("/profile/refine", json={"text": "participated in a hackathon"})
        self.assertEqual(res.json(), {"refined": "Participated in a hackathon.", "changed": True, "minor": True})

    def test_refine_failure_leaves_the_text_alone(self):
        with patch.object(pr, "build_server_llm", return_value=object()), \
             patch.object(pr, "build_refine_chain", chain_returning(error=Exception("down"))):
            res = self.client.post("/profile/refine", json={"text": "led the robotics club"})
        self.assertEqual(res.status_code, 502)
        self.assertIn("unchanged", res.json()["detail"])

    def test_add_achievement_keeps_the_original_wording(self):
        self.db.query.return_value.filter.return_value.count.return_value = 0
        res = self.client.post("/profile/achievements", json={
            "category": "hackathon", "text": "Won 2nd place at HackX 2025", "original_text": "i got 2nd place in hackx 2025"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["achievement"]["original_text"], "i got 2nd place in hackx 2025")

    def test_unknown_category_becomes_other(self):
        self.db.query.return_value.filter.return_value.count.return_value = 0
        res = self.client.post("/profile/achievements", json={"category": "wizardry", "text": "Did a thing well"})
        self.assertEqual(res.json()["achievement"]["category"], "other")

    def test_achievement_cap(self):
        self.db.query.return_value.filter.return_value.count.return_value = pr.MAX_ACHIEVEMENTS
        self.assertEqual(self.client.post("/profile/achievements", json={"text": "One more thing"}).status_code, 409)


class TestProfilePool(unittest.TestCase):
    def _pool(self, projects, achievements):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [projects, achievements]
        return pr.build_profile_addons(uuid.uuid4(), db)

    def test_empty_pool_adds_nothing_to_the_prompt(self):
        self.assertEqual(self._pool([], []), "")

    def test_pool_lists_items_and_says_using_none_is_fine(self):
        text = self._pool([make_project(status="approved", name="Sculpt", summary="Resume optimizer")],
                          [SimpleNamespace(category="hackathon", text="Won 2nd place at HackX 2025")])
        self.assertIn("1. Sculpt: Resume optimizer | Tech: Python", text)
        self.assertIn("   - Built a", text)
        self.assertIn("- [hackathon] Won 2nd place at HackX 2025", text)
        self.assertIn("using none of it is a valid choice", text)

    def test_optimizer_receives_the_pool(self):
        from schema.schema import parsedJobDescription
        user = MagicMock(id=uuid.uuid4(), email="u@example.com", plan="free", resume_yaml="name: Jane\n",
                         email_verified=True, is_byok_allowed=False, selected_template="jakes_resume")
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: MagicMock()
        workflow = AsyncMock(return_value={"final_yaml": "name: Jane\nskills: [Python]\n", "final_latex": "t",
                                           "current_ats_score": 80.0, "score_history": [{}],
                                           "current_ats_details": {"match_level": "Good"}})
        jd = parsedJobDescription(job_title="Eng", skills=["Python"], job_description="d")
        try:
            with patch.object(main, "resolve_request_llm", return_value=object()), \
                 patch.object(main, "get_or_parse_jd", AsyncMock(return_value=(jd, "id"))), \
                 patch.object(main, "optimize_resume_agentic", workflow), \
                 patch.object(main, "reserve_generation_slot", return_value=(main.get_week_start(), 1)), \
                 patch.object(main, "build_profile_addons", return_value="PROFILE POOL. x"), \
                 patch.object(main, "get_daily_usage", return_value=1), \
                 patch.object(main, "get_monthly_usage", return_value=1):
                res = TestClient(app).post("/optimize-resume", json={"job_desc": "Python engineer " * 10, "original_ats_score": 60})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(workflow.call_args.kwargs["addons"], "PROFILE POOL. x")
        finally:
            app.dependency_overrides.clear()


class TestGitHubConnect(Base):
    ENV = {"GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "secret", "DASHBOARD_URL": "http://dash.test"}

    def test_start_asks_for_public_access_by_default(self):
        with patch.dict(os.environ, self.ENV):
            url = self.client.get("/auth/github/start").json()["url"]
        self.assertIn("client_id=cid", url)
        self.assertIn("state=", url)
        self.assertNotIn("scope=", url)

    def test_private_access_is_opt_in(self):
        with patch.dict(os.environ, self.ENV):
            url = self.client.get("/auth/github/start?private=true").json()["url"]
        self.assertIn("scope=repo", url)

    def test_start_without_credentials_is_503(self):
        with patch.dict(os.environ, {"GITHUB_CLIENT_ID": "", "GITHUB_CLIENT_SECRET": ""}):
            self.assertEqual(self.client.get("/auth/github/start").status_code, 503)

    def _callback(self, state, other_user=None, scopes=frozenset()):
        self.db.query.return_value.filter.return_value.first.side_effect = [self.user, other_user]
        client = MagicMock()
        client.user = AsyncMock(return_value={"id": 777, "login": "janedev"})
        with patch.dict(os.environ, self.ENV), \
             patch.object(pr.gh, "exchange_code", AsyncMock(return_value=("gho_new", set(scopes)))), \
             patch.object(pr.gh, "GitHub", return_value=client):
            return self.client.get(f"/auth/github/callback?code=abc&state={state}", follow_redirects=False)

    def _state(self, **claims):
        return pr.create_access_token({"sub": str(self.user.id), "purpose": "github_oauth", **claims},
                                      expires_delta=timedelta(minutes=10))

    def test_callback_links_the_account_and_stores_the_token_encrypted(self):
        res = self._callback(self._state())
        self.assertEqual(res.headers["location"], "http://dash.test/?github=connected")
        self.assertEqual(self.user.github_username, "janedev")
        self.assertEqual(self.user.github_id, "777")
        self.assertNotIn("gho_new", self.user.github_token_enc)
        self.assertEqual(gh.decrypt_token(self.user.github_token_enc), "gho_new")
        self.assertFalse(self.user.github_private_access)

    def test_callback_records_private_access_when_granted(self):
        self._callback(self._state(private=True), scopes={"repo"})
        self.assertTrue(self.user.github_private_access)

    def test_github_account_already_linked_elsewhere(self):
        res = self._callback(self._state(), other_user=make_user())
        self.assertEqual(res.headers["location"], "http://dash.test/?github=in_use")

    def test_forged_or_wrong_purpose_state_is_refused(self):
        with patch.dict(os.environ, self.ENV):
            forged = self.client.get("/auth/github/callback?code=abc&state=not.a.jwt", follow_redirects=False)
            session_token = pr.create_access_token({"sub": str(self.user.id)})  # a login token is not a state
            wrong = self.client.get(f"/auth/github/callback?code=abc&state={session_token}", follow_redirects=False)
        self.assertEqual(forged.headers["location"], "http://dash.test/?github=expired")
        self.assertEqual(wrong.headers["location"], "http://dash.test/?github=expired")

    def test_user_cancelling_on_github_comes_back_cleanly(self):
        with patch.dict(os.environ, self.ENV):
            res = self.client.get("/auth/github/callback?error=access_denied&state=x", follow_redirects=False)
        self.assertEqual(res.headers["location"], "http://dash.test/?github=cancelled")


class TestRepoReader(unittest.TestCase):
    def test_token_round_trip_and_tamper(self):
        blob = gh.encrypt_token("gho_secret")
        self.assertEqual(gh.decrypt_token(blob), "gho_secret")
        self.assertIsNone(gh.decrypt_token("garbage"))
        self.assertIsNone(gh.decrypt_token(None))

    def test_skips_noise_and_picks_manifests_and_entry_points(self):
        files = [("node_modules/x/index.js", 900), ("package-lock.json", 90_000), ("logo.png", 5000),
                 ("package.json", 800), ("src/app.py", 4000), ("src/deep/a/b/helper.py", 2000),
                 ("tests/test_app.py", 3000), ("README.md", 2000)]
        kept = [f for f in files if gh._keep(f[0])]
        self.assertNotIn(("node_modules/x/index.js", 900), kept)
        self.assertNotIn(("package-lock.json", 90_000), kept)
        picked = [p for p, _ in gh._pick_files(kept)]
        self.assertEqual(picked[0], "package.json")
        self.assertIn("src/app.py", picked)
        self.assertIn("src/deep/a/b/helper.py", picked)   # deep files still qualify, just rank lower
        self.assertNotIn("tests/test_app.py", picked)
        self.assertLess(picked.index("src/app.py"), picked.index("src/deep/a/b/helper.py"))

    def _digest(self, meta, contributors):
        class Res:
            def __init__(self, code, body=None, text=""):
                self.status_code, self._body, self.text, self.headers = code, body, text, {}

            def json(self):
                return self._body

        async def fake_get(client, path, raw=False, params=None):
            if path.endswith("/contributors"):
                return Res(200, contributors)
            if path.endswith("/languages"):
                return Res(200, {"Python": 1000})
            if path.endswith("/readme"):
                return Res(200, text="# Proj\nDoes things.")
            if "/git/trees/" in path:
                return Res(200, {"tree": [{"type": "blob", "path": "app.py", "size": 2000}]})
            if "/contents/" in path:
                return Res(200, text="print('hi')")
            return Res(200, meta)

        client = gh.GitHub("t")
        with patch.object(gh.GitHub, "_get", side_effect=fake_get):
            return asyncio.run(client.repo_digest("octo/proj", "jane"))

    def test_contributor_to_someone_elses_repo_is_accepted(self):
        d = self._digest({"name": "proj", "owner": {"login": "octo"}, "fork": False, "default_branch": "main"},
                         [{"login": "octo", "contributions": 90}, {"login": "Jane", "contributions": 10}])
        self.assertIn("made 10 of 100 commits", d["context"])
        self.assertIn("FILE: app.py", d["context"])

    def test_own_repo_is_accepted(self):
        d = self._digest({"name": "proj", "owner": {"login": "jane"}, "fork": False, "default_branch": "main"}, [])
        self.assertIn("the candidate owns the repository", d["context"])

    def test_untouched_fork_of_someone_elses_work_is_refused(self):
        with self.assertRaises(gh.GitHubError) as ctx:
            self._digest({"name": "linux", "owner": {"login": "jane"}, "fork": True, "default_branch": "main"},
                         [{"login": "torvalds", "contributions": 9999}])
        self.assertIn("contributor", str(ctx.exception))

    def test_stranger_repo_is_refused(self):
        with self.assertRaises(gh.GitHubError):
            self._digest({"name": "proj", "owner": {"login": "octo"}, "fork": False, "default_branch": "main"},
                         [{"login": "octo", "contributions": 5}])


if __name__ == "__main__":
    unittest.main()
