"""Staff positions and the committee proposal waiting for its second signature (T20)."""

from alembic import op
import sqlalchemy as sa

revision = '0004_staff_actions'
down_revision = '0003_workflow'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as t:
        t.add_column(sa.Column('staff_position', sa.String(), nullable=True))
    with op.batch_alter_table('loan_applications') as t:
        t.add_column(sa.Column('pending_review', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('loan_applications') as t:
        t.drop_column('pending_review')
    with op.batch_alter_table('users') as t:
        t.drop_column('staff_position')
