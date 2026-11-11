"""Index users.password_reset_token_hash.

The model declares this index, but create_all() never adds indexes to a table that
already exists, so the live database was missing it. Reset-token lookups scan users
without it.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_password_reset_token_hash "
        "ON users (password_reset_token_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_password_reset_token_hash")
