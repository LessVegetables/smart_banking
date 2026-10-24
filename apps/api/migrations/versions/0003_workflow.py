"""Add v2 applications and their audit log (T19); the fact store now belongs to v2 applications."""

from alembic import op
import sqlalchemy as sa

revision = '0003_workflow'
down_revision = '0002_fact_store'
branch_labels = None
depends_on = None


def _facts_table(parent: str):
    # No workflow wrote facts before T19, so the table is recreated rather than altered: SQLite can't
    # change a foreign key in place, and 0002 left the constraint unnamed.
    op.create_table(
        'application_facts',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey(f'{parent}.id', ondelete='CASCADE'), nullable=False),
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


def _drop_facts_table():
    op.drop_index('ix_application_facts_name', table_name='application_facts')
    op.drop_index('ix_application_facts_application_id', table_name='application_facts')
    op.drop_table('application_facts')


def upgrade():
    op.create_table(
        'loan_applications',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('request', sa.JSON(), nullable=False),
        sa.Column('enrichment', sa.JSON(), nullable=True),
        sa.Column('data_quality', sa.JSON(), nullable=True),
        sa.Column('decision', sa.JSON(), nullable=True),
        sa.Column('offer_valid_until', sa.String(), nullable=True),
        sa.Column('selected_offer_id', sa.String(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('lease_until', sa.Float(), nullable=True),
        sa.Column('created', sa.String(), nullable=False),
        sa.Column('updated', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_loan_applications_user_id', 'loan_applications', ['user_id'])
    op.create_index('ix_loan_applications_status', 'loan_applications', ['status'])
    op.create_table(
        'application_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey('loan_applications.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('at', sa.String(), nullable=False),
        sa.Column('actor', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('from_status', sa.String(), nullable=True),
        sa.Column('to_status', sa.String(), nullable=True),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_application_events_application_id', 'application_events', ['application_id'])
    _drop_facts_table()
    _facts_table('loan_applications')


def downgrade():
    _drop_facts_table()
    _facts_table('applications')
    op.drop_index('ix_application_events_application_id', table_name='application_events')
    op.drop_table('application_events')
    op.drop_index('ix_loan_applications_status', table_name='loan_applications')
    op.drop_index('ix_loan_applications_user_id', table_name='loan_applications')
    op.drop_table('loan_applications')
