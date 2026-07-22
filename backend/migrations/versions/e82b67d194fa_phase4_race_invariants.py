"""phase4 race and tombstone invariants

Revision ID: e82b67d194fa
Revises: d7f31a8be901
"""
from typing import Sequence, Union
from alembic import op

revision: str="e82b67d194fa"
down_revision: Union[str,None]="d7f31a8be901"
branch_labels: Union[str,Sequence[str],None]=None
depends_on: Union[str,Sequence[str],None]=None

def upgrade()->None:
    op.create_check_constraint("ck_events_merged_hidden","events","merged_into_event_id IS NULL OR publication_status = 'hidden'")

def downgrade()->None:
    op.drop_constraint("ck_events_merged_hidden","events",type_="check")
