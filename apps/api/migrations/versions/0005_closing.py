"""Documents, signature, pre-disbursement conditions and payout of a v2 application (T21)."""

from alembic import op
import sqlalchemy as sa

revision = '0005_closing'
down_revision = '0004_staff_actions'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('loan_applications') as t:
        t.add_column(sa.Column('closing', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('loan_applications') as t:
        t.drop_column('closing')
