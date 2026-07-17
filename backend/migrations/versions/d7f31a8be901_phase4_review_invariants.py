"""phase4 review and revision invariants

Revision ID: d7f31a8be901
Revises: c1e5f00d4a42
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str="d7f31a8be901"
down_revision: Union[str,None]="c1e5f00d4a42"
branch_labels: Union[str,Sequence[str],None]=None
depends_on: Union[str,Sequence[str],None]=None

def upgrade()->None:
    op.add_column("events",sa.Column("merged_into_event_id",sa.UUID(),nullable=True))
    op.create_index("ix_events_merged_into_event_id","events",["merged_into_event_id"])
    op.create_foreign_key("fk_events_merged_into_event","events","events",["merged_into_event_id"],["id"],ondelete="RESTRICT")
    op.drop_constraint("fk_events_current_revision","events",type_="foreignkey")
    op.create_unique_constraint("uq_event_revision_identity","event_revisions",["id","event_id"])
    op.create_foreign_key("fk_events_current_revision_same_event","events","event_revisions",["current_revision_id","id"],["id","event_id"],ondelete="RESTRICT")

def downgrade()->None:
    op.drop_constraint("fk_events_current_revision_same_event","events",type_="foreignkey")
    op.drop_constraint("uq_event_revision_identity","event_revisions",type_="unique")
    op.create_foreign_key("fk_events_current_revision","events","event_revisions",["current_revision_id"],["id"],ondelete="SET NULL")
    op.drop_constraint("fk_events_merged_into_event","events",type_="foreignkey")
    op.drop_index("ix_events_merged_into_event_id",table_name="events")
    op.drop_column("events","merged_into_event_id")
