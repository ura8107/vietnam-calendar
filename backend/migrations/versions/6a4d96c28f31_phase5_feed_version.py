"""Add optimistic version to feed settings.

Revision ID: 6a4d96c28f31
Revises: f52339e6d32a
"""
from alembic import op
import sqlalchemy as sa

revision="6a4d96c28f31"
down_revision="f52339e6d32a"
branch_labels=None
depends_on=None

def upgrade()->None:
    op.add_column("feeds",sa.Column("version",sa.Integer(),server_default="1",nullable=False))
    op.create_check_constraint("ck_feeds_version","feeds","version >= 1")

def downgrade()->None:
    op.drop_constraint("ck_feeds_version","feeds",type_="check")
    op.drop_column("feeds","version")
