"""Overdraft pre-approvals (T22): the latest batch result per client."""

from alembic import op
import sqlalchemy as sa

revision = '0006_preapprovals'
down_revision = '0005_closing'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'preapprovals',
        sa.Column('inn', sa.String(), nullable=False),
        sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=True),
        sa.Column('computed_at', sa.String(), nullable=False),
        sa.Column('valid_until', sa.String(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('inn'),
    )
    op.create_index('ix_preapprovals_computed_at', 'preapprovals', ['computed_at'])


def downgrade():
    op.drop_index('ix_preapprovals_computed_at', table_name='preapprovals')
    op.drop_table('preapprovals')
