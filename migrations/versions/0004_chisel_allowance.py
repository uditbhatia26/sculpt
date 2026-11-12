"""Chisel allowance period replaces the per-project lock.

A period starts with a user's first Chisel add and lasts 30 days; the adds made in it are
counted on the user. Projects are no longer locked, so existing locks are cleared. Users who
already added Chisel projects in the last 30 days start with those adds counted.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "chisel_period_start" not in have:
        op.add_column("users", sa.Column("chisel_period_start", sa.DateTime(timezone=True), nullable=True))
    if "chisel_period_adds" not in have:
        op.add_column("users", sa.Column("chisel_period_adds", sa.Integer(), nullable=False, server_default="0"))

    op.execute("""
        UPDATE users AS u
        SET chisel_period_start = recent.first_add, chisel_period_adds = recent.adds
        FROM (
            SELECT user_id, MIN(created_at) AS first_add, COUNT(*) AS adds
            FROM profile_projects
            WHERE source = 'github' AND created_at > NOW() - INTERVAL '30 days'
            GROUP BY user_id
        ) AS recent
        WHERE u.id = recent.user_id AND u.chisel_period_start IS NULL
    """)
    op.execute("UPDATE profile_projects SET locked_until = NULL WHERE locked_until IS NOT NULL")


def downgrade() -> None:
    op.drop_column("users", "chisel_period_adds")
    op.drop_column("users", "chisel_period_start")
