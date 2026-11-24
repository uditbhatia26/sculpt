"""Daily allowance for server-paid AI calls without a weekly quota.

One row per user, UTC day and kind (upload, jd_parse, explain, refine). See
config/ai_allowance.py.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ai_daily_usage"):
        return
    op.create_table(
        "ai_daily_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("bucket", sa.String(32), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "day", "bucket", name="uq_ai_daily_usage_user_day_bucket"),
    )
    op.create_index("ix_ai_daily_usage_user_id", "ai_daily_usage", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_daily_usage_user_id", table_name="ai_daily_usage")
    op.drop_table("ai_daily_usage")
