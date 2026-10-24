"""Initial schema, also used to adopt databases created before Alembic."""

from alembic import op
import sqlalchemy as sa

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('identity', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('identity'),
    )
    op.create_table(
        'applications',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('submission_key', sa.String(), nullable=False),
        sa.Column('survey_id', sa.String(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=False),
        sa.Column('policy_snapshot', sa.JSON(), nullable=False),
        sa.Column('decisions', sa.JSON(), nullable=False),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('selected_offer', sa.JSON(), nullable=True),
        sa.Column('offer_expires', sa.Float(), nullable=True),
        sa.Column('documents_reviewed', sa.Integer(), nullable=False),
        sa.Column('created', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_key'),
    )
    op.create_index('ix_applications_user_id', 'applications', ['user_id'])
    op.create_table(
        'sessions',
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('expires', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_table(
        'surveys',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=False),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('created', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_surveys_user_id', 'surveys', ['user_id'])
    op.create_table(
        'documents',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey('applications.id'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('media_type', sa.String(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_documents_application_id', 'documents', ['application_id'])
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey('applications.id'), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('available', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_jobs_application_id', 'jobs', ['application_id'])
    op.create_table(
        'submission_keys',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), sa.ForeignKey('applications.id'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade():
    op.drop_table('submission_keys')
    op.drop_index('ix_jobs_application_id', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index('ix_documents_application_id', table_name='documents')
    op.drop_table('documents')
    op.drop_index('ix_surveys_user_id', table_name='surveys')
    op.drop_table('surveys')
    op.drop_table('sessions')
    op.drop_index('ix_applications_user_id', table_name='applications')
    op.drop_table('applications')
    op.drop_table('users')
