"""Statements from other banks uploaded by the client (T23)."""

from alembic import op
import sqlalchemy as sa

revision = '0007_statements'
down_revision = '0006_preapprovals'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('loan_applications') as t:
        t.add_column(sa.Column('statements', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('loan_applications') as t:
        t.drop_column('statements')
