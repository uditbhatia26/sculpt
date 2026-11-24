"""Weekly ATS analysis allowance.

Analyze compatibility gets its own weekly count, kept on the same generation_usage row as
generations (one row per user per ISO week), so an analysis never uses up a generation.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("generation_usage")}
    if "analysis_count" not in have:
        op.add_column("generation_usage", sa.Column("analysis_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("generation_usage", "analysis_count")
