"""Billing: a Razorpay subscription on each user, and the payments it made.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("users")}
    if "razorpay_subscription_id" not in have:
        op.add_column("users", sa.Column("razorpay_subscription_id", sa.String(), nullable=True))
        op.create_index("ix_users_razorpay_subscription_id", "users", ["razorpay_subscription_id"])
    if "subscription_status" not in have:
        op.add_column("users", sa.Column("subscription_status", sa.String(), nullable=True))
    if "pro_until" not in have:
        op.add_column("users", sa.Column("pro_until", sa.DateTime(timezone=True), nullable=True))

    if "payments" not in insp.get_table_names():
        op.create_table(
            "payments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("razorpay_payment_id", sa.String(), nullable=False, unique=True),
            sa.Column("razorpay_subscription_id", sa.String(), nullable=True),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(), nullable=False, server_default="INR"),
            sa.Column("status", sa.String(), nullable=False, server_default="captured"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_payments_user_id", "payments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_users_razorpay_subscription_id", table_name="users")
    op.drop_column("users", "pro_until")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "razorpay_subscription_id")
