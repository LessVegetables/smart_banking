"""Add the normalized application fact store."""

from alembic import op
import sqlalchemy as sa

revision = '0002_fact_store'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'application_facts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey('applications.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('source_key', sa.String(), nullable=False),
        sa.Column('declared_value', sa.JSON(), nullable=True),
        sa.Column('verified_value', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('trust_level', sa.String(), nullable=False),
        sa.Column('fetched_at', sa.String(), nullable=True),
        sa.Column('discrepancy', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', 'name', 'source_key', name='uq_application_fact_source'),
    )
    op.create_index('ix_application_facts_application_id', 'application_facts', ['application_id'])
    op.create_index('ix_application_facts_name', 'application_facts', ['name'])


def downgrade():
    op.drop_index('ix_application_facts_name', table_name='application_facts')
    op.drop_index('ix_application_facts_application_id', table_name='application_facts')
    op.drop_table('application_facts')
