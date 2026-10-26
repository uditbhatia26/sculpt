"""Baseline: creates any missing tables and applies the idempotent column adds
that init_db.run_migrations() used to handle. Safe on an existing database.

For an existing DB just run:  alembic stamp head
For a new DB run:             alembic upgrade head

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from config.database import Base
    import models.database_models  # noqa: F401

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)

    for stmt in (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_byok_allowed BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ",
    ):
        op.execute(sa.text(stmt))


def downgrade() -> None:
    # The baseline is intentionally not reversible (would drop user data).
    pass
