"""
test_pillar1_byok.py
====================
Automated test suite for Pillar 1 — Entitlement-Gated BYOK & Hybrid Model (Admin Management):
1. Schema & DB Columns (User model attributes, AuthResponse & UserProfile serialization, optional ai_config)
2. LLM Resolution (Standard user server fallback, BYOK user custom key, Non-entitled rejection)
3. Endpoint Security & Quota Gates (/validate-key gated by BYOK, /admin/* gated by admin role)
4. Admin State Mutations (PATCH /admin/users/{id}/byok, PATCH /admin/users/{id}/plan)
"""

import os
import sys
import uuid
import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Ensure backend root is on path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from models.database_models import User
from schema.schema import (
    AuthResponse,
    UserProfile,
    CalculateATS,
    OptimizeResumeRequest,
    ExplainChangesRequest,
    AIProviderConfig,
    AdminUserListResponse,
    AdminUserItem,
    UpdateUserBYOKRequest,
    UpdateUserPlanRequest,
)
from main import (
    app,
    resolve_request_llm,
    get_current_user,
    get_current_admin,
    get_db,
)


class TestPillar1BYOK(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ── Test Suite 1: Schema & DB Columns ───────────────────────────────────────
    def test_01_user_model_has_entitlement_columns(self):
        """Verify User model has is_byok_allowed and is_admin boolean columns."""
        cols = User.__table__.columns
        self.assertIn("is_byok_allowed", cols, "User model must have is_byok_allowed column")
        self.assertIn("is_admin", cols, "User model must have is_admin column")

        # Test defaults
        user = User(email="test_model@example.com")
        self.assertFalse(user.is_byok_allowed)
        self.assertFalse(user.is_admin)

    def test_02_auth_and_user_schemas_serialize_entitlements(self):
        """Verify AuthResponse and UserProfile schemas serialize entitlement flags."""
        auth_resp = AuthResponse(
            access_token="fake.token.jwt",
            token_type="bearer",
            user_id="user-123",
            email="test@example.com",
            full_name="Test User",
            plan="free",
            has_resume=False,
            weekly_usage=0,
            weekly_limit=5,
            is_byok_allowed=True,
            is_admin=False,
        )
        self.assertTrue(auth_resp.is_byok_allowed)
        self.assertFalse(auth_resp.is_admin)

        profile = UserProfile(
            user_id="user-123",
            email="test@example.com",
            full_name="Admin User",
            plan="free",
            has_resume=False,
            resume_filename=None,
            resume_uploaded_at=None,
            is_byok_allowed=False,
            is_admin=True,
            weekly_usage=2,
            weekly_limit=5,
            created_at="2026-01-01T00:00:00",
        )
        self.assertFalse(profile.is_byok_allowed)
        self.assertTrue(profile.is_admin)

    def test_03_request_schemas_allow_optional_ai_config(self):
        """Verify CalculateATS, OptimizeResumeRequest, and ExplainChangesRequest accept ai_config=None."""
        jd_sample = "We are seeking a senior full stack software engineer with extensive experience in React, Python, and cloud services."
        ats_req = CalculateATS(job_desc=jd_sample)
        self.assertIsNone(ats_req.ai_config)

        opt_req = OptimizeResumeRequest(job_desc=jd_sample)
        self.assertIsNone(opt_req.ai_config)

        exp_req = ExplainChangesRequest(optimized_yaml="name: Test Candidate")
        self.assertIsNone(exp_req.ai_config)

    # ── Test Suite 2: LLM Resolution ───────────────────────────────────────────
    def test_04_standard_user_resolves_to_server_llm(self):
        """Standard user without BYOK resolves to server .env fallback key."""
        standard_user = User(
            id=uuid.uuid4(),
            email="standard@example.com",
            is_byok_allowed=False,
            is_admin=False,
            plan="free",
        )
        mock_db = MagicMock()

        # Mock the server environment to ensure a key is present
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "GROQ_API_KEY": "gsk_server_test_key_abc"}):
            with patch("main._build_llm_from_config") as mock_build:
                mock_build.return_value = MagicMock(name="ServerLLM")
                llm = resolve_request_llm(
                    user=standard_user,
                    client_config=None,
                    db=mock_db,
                    enforce_quota=False,
                )
                self.assertIsNotNone(llm)
                mock_build.assert_called_once()
                config_used = mock_build.call_args[0][0]
                self.assertEqual(config_used.api_key, "gsk_server_test_key_abc")

    def test_05_byok_entitled_user_builds_client_llm(self):
        """BYOK-entitled user with client_config builds custom provider LLM."""
        byok_user = User(
            id=uuid.uuid4(),
            email="byok_pro@example.com",
            is_byok_allowed=True,
            is_admin=False,
            plan="free",
        )
        mock_db = MagicMock()
        client_cfg = AIProviderConfig(
            provider="openai",
            api_key="sk-client-key-xyz12345",
            model="gpt-4o",
        )

        with patch("main._build_llm_from_config") as mock_build:
            mock_build.return_value = MagicMock(name="ClientLLM")
            llm = resolve_request_llm(
                user=byok_user,
                client_config=client_cfg,
                db=mock_db,
                enforce_quota=True,  # BYOK should bypass quota
            )
            self.assertIsNotNone(llm)
            mock_build.assert_called_once_with(client_cfg)

    def test_06_non_entitled_user_submitting_key_rejected(self):
        """Non-entitled user attempting to use custom API key raises HTTP 403."""
        non_entitled_user = User(
            id=uuid.uuid4(),
            email="sneaky@example.com",
            is_byok_allowed=False,
            is_admin=False,
            plan="free",
        )
        mock_db = MagicMock()
        sneaky_cfg = AIProviderConfig(
            provider="groq",
            api_key="gsk_sneaky_key",
        )

        with self.assertRaises(HTTPException) as ctx:
            resolve_request_llm(
                user=non_entitled_user,
                client_config=sneaky_cfg,
                db=mock_db,
                enforce_quota=False,
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("BYOK is only available for entitled accounts", ctx.exception.detail)

    # ── Test Suite 3: Security & Quotas ─────────────────────────────────────────
    def test_07_validate_key_gated_by_byok_entitlement(self):
        """POST /validate-key must return 403 Forbidden for non-BYOK users."""
        non_entitled = User(
            id=uuid.uuid4(),
            email="regular@example.com",
            is_byok_allowed=False,
            is_admin=False,
        )

        app.dependency_overrides[get_current_user] = lambda: non_entitled
        try:
            resp = self.client.post(
                "/validate-key",
                json={"provider": "groq", "api_key": "gsk_test12345678"},
            )
            self.assertEqual(resp.status_code, 403)
            self.assertIn("BYOK is only available for entitled accounts", resp.json()["detail"])
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_08_admin_endpoints_gate_non_admins(self):
        """GET /admin/users must return 403 Forbidden for non-admin users."""
        standard_user = User(
            id=uuid.uuid4(),
            email="standard@example.com",
            is_byok_allowed=False,
            is_admin=False,
        )

        app.dependency_overrides[get_current_user] = lambda: standard_user
        try:
            resp = self.client.get("/admin/users")
            self.assertEqual(resp.status_code, 403)
            self.assertIn("Admin privileges required", resp.json()["detail"])
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_09_admin_can_access_user_list(self):
        """GET /admin/users returns 200 OK for admin user."""
        admin_user = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            is_byok_allowed=True,
            is_admin=True,
            created_at=None,
        )

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 1
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [admin_user]

        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_db] = lambda: mock_db
        try:
            # 1. Without search
            resp = self.client.get("/admin/users")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("users", data)
            self.assertEqual(data["total_count"], 1)

            # 2. With search term (exercises func.lower and or_ in line 1488)
            resp_search = self.client.get("/admin/users?search=admin")
            self.assertEqual(resp_search.status_code, 200)
            self.assertIn("users", resp_search.json())
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)

    # ── Test Suite 4: Admin State Mutations ─────────────────────────────────────
    def test_10_admin_mutate_user_byok(self):
        """PATCH /admin/users/{id}/byok successfully toggles is_byok_allowed."""
        admin_user = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            is_byok_allowed=True,
            is_admin=True,
        )
        target_user = User(
            id=uuid.uuid4(),
            email="target@example.com",
            is_byok_allowed=False,
            is_admin=False,
            plan="free",
        )

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value.first.return_value = target_user

        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_db] = lambda: mock_db
        try:
            resp = self.client.patch(
                f"/admin/users/{target_user.id}/byok",
                json={"is_byok_allowed": True},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(target_user.is_byok_allowed)
            mock_db.commit.assert_called_once()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)

    def test_11_admin_mutate_user_plan(self):
        """PATCH /admin/users/{id}/plan successfully updates user plan."""
        admin_user = User(
            id=uuid.uuid4(),
            email="admin@example.com",
            is_byok_allowed=True,
            is_admin=True,
        )
        target_user = User(
            id=uuid.uuid4(),
            email="target@example.com",
            is_byok_allowed=False,
            is_admin=False,
            plan="free",
        )

        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value.first.return_value = target_user

        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_db] = lambda: mock_db
        try:
            resp = self.client.patch(
                f"/admin/users/{target_user.id}/plan",
                json={"plan": "pro"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(target_user.plan, "pro")
            mock_db.commit.assert_called_once()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
