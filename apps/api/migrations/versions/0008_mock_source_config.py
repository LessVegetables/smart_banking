"""Shared organizer settings for the API and worker."""
from alembic import op
import sqlalchemy as sa
revision = '0008_mock_source_config'
down_revision = '0007_statements'
branch_labels = None
depends_on = None


def upgrade():
    table=op.create_table('mock_source_config',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('settings',sa.JSON(),nullable=False),
        sa.Column('updated_at',sa.String(),nullable=True),
        sa.Column('updated_by',sa.String(),nullable=True))
    op.bulk_insert(table,[{'id':1,'revision':1,'settings':{}}])


def downgrade():
    op.drop_table('mock_source_config')
