"""ai run job attempt correlation

Revision ID: a91d6c42f3b0
Revises: 57ef74b846aa
"""
from alembic import op
import sqlalchemy as sa
revision="a91d6c42f3b0"; down_revision="57ef74b846aa"; branch_labels=None; depends_on=None
def upgrade():
    op.add_column("ai_runs",sa.Column("job_id",sa.UUID(),nullable=True))
    op.add_column("ai_runs",sa.Column("attempt_number",sa.Integer(),nullable=True))
    op.create_foreign_key("fk_ai_runs_job","ai_runs","jobs",["job_id"],["id"],ondelete="SET NULL")
    op.create_index("ix_ai_runs_job_id","ai_runs",["job_id"])
def downgrade():
    op.drop_index("ix_ai_runs_job_id",table_name="ai_runs"); op.drop_constraint("fk_ai_runs_job","ai_runs",type_="foreignkey"); op.drop_column("ai_runs","attempt_number"); op.drop_column("ai_runs","job_id")
