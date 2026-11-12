"""Profile pool and onboarding: GitHub connection, Chisel projects, achievements.

Accounts that already have a resume are grandfathered: they start at the projects step
and keep using the extension while they finish the new steps on the website.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # On a fresh database the baseline (0001) builds every table from the current models,
    # so each step here is skipped if its column or table is already present.
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("users")}
    tables = set(insp.get_table_names())

    def add(column: sa.Column) -> None:
        if column.name not in have:
            op.add_column("users", column)

    add(sa.Column("onboarding_step", sa.String(), nullable=False, server_default="resume"))
    add(sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    add(sa.Column("onboarding_grandfathered", sa.Boolean(), nullable=False, server_default="false"))
    add(sa.Column("github_id", sa.String(), nullable=True))
    add(sa.Column("github_username", sa.String(), nullable=True))
    add(sa.Column("github_token_enc", sa.Text(), nullable=True))
    add(sa.Column("github_private_access", sa.Boolean(), nullable=False, server_default="false"))
    add(sa.Column("chisel_generations_used", sa.Integer(), nullable=False, server_default="0"))
    if "github_id" not in have:
        op.create_unique_constraint("uq_users_github_id", "users", ["github_id"])

    # Existing accounts: never locked out of the extension
    op.execute("UPDATE users SET onboarding_grandfathered = TRUE")
    op.execute("UPDATE users SET onboarding_step = 'projects' WHERE resume_yaml IS NOT NULL")

    if "profile_projects" in tables and "profile_achievements" in tables:
        return

    op.create_table(
        "profile_projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tech_stack", JSONB(), nullable=True),
        sa.Column("bullets", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("repo_full_name", sa.String(), nullable=True),
        sa.Column("repo_url", sa.String(), nullable=True),
        sa.Column("repo_private", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("facts", sa.Text(), nullable=True),
        sa.Column("repo_context", sa.Text(), nullable=True),
        sa.Column("ai_edits_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_profile_projects_user_id", "profile_projects", ["user_id"])

    op.create_table(
        "profile_achievements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="other"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_profile_achievements_user_id", "profile_achievements", ["user_id"])


def downgrade() -> None:
    op.drop_table("profile_achievements")
    op.drop_table("profile_projects")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_github_id")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_github_id_key")
    for col in ("chisel_generations_used", "github_private_access", "github_token_enc", "github_username",
                "github_id", "onboarding_grandfathered", "onboarding_completed_at", "onboarding_step"):
        op.drop_column("users", col)
