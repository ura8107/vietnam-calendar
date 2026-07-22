"""Phase 5 calendar projection index.

Revision ID: f52339e6d32a
Revises: e82b67d194fa
"""
from alembic import op
import sqlalchemy as sa

revision = "f52339e6d32a"
down_revision = "e82b67d194fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_events_calendar", "events", ["publication_status", "event_date"],
        unique=False, postgresql_where=sa.text("merged_into_event_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_events_calendar", table_name="events")
