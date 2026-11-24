"""Save "Explain changes" on the optimization it explains.

The first explanation for an optimized resume is stored on its optimized_resumes row and
returned from there afterwards, so it is never generated twice.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("optimized_resumes")}
    if "change_explanation" not in have:
        op.add_column("optimized_resumes", sa.Column("change_explanation", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("optimized_resumes", "change_explanation")
