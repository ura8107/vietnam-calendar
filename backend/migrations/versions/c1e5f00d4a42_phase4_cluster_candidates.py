"""phase4 cluster candidates

Revision ID: c1e5f00d4a42
Revises: a91d6c42f3b0
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c1e5f00d4a42"
down_revision: Union[str, None] = "a91d6c42f3b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    status=sa.Enum("pending","accepted","dismissed",name="cluster_candidate_status")
    status.create(op.get_bind(),checkfirst=True)
    op.create_table("event_cluster_candidates",
        sa.Column("id",sa.UUID(),nullable=False),
        sa.Column("event_id",sa.UUID(),nullable=False),
        sa.Column("candidate_event_id",sa.UUID(),nullable=False),
        sa.Column("similarity_score",sa.Numeric(5,4),nullable=False),
        sa.Column("reasons",postgresql.JSONB(),nullable=False),
        sa.Column("status",status,nullable=False),
        sa.Column("reviewed_by_id",sa.UUID(),nullable=True),
        sa.Column("reviewed_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.text("now()"),nullable=False),
        sa.CheckConstraint("event_id <> candidate_event_id",name="ck_event_cluster_distinct"),
        sa.CheckConstraint("similarity_score BETWEEN 0 AND 1",name="ck_event_cluster_similarity"),
        sa.ForeignKeyConstraint(["event_id"],["events.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_event_id"],["events.id"],ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"],["users.id"],ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),sa.UniqueConstraint("event_id","candidate_event_id",name="uq_event_cluster_pair"))
    op.create_index("ix_event_cluster_candidates_event_id","event_cluster_candidates",["event_id"])
    op.create_index("ix_event_cluster_candidates_candidate_event_id","event_cluster_candidates",["candidate_event_id"])
    op.create_index("ix_event_cluster_candidates_status","event_cluster_candidates",["status"])

def downgrade() -> None:
    op.drop_table("event_cluster_candidates")
    sa.Enum(name="cluster_candidate_status").drop(op.get_bind(),checkfirst=True)
