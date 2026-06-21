"""add teacher approval status

Revision ID: c84f319b0216
Revises: b73d2e9c5a14
Create Date: 2026-06-21

"""
from alembic import op
import sqlalchemy as sa


revision = 'c84f319b0216'
down_revision = 'b73d2e9c5a14'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'approval_status',
            sa.String(length=20),
            nullable=False,
            server_default='approved'
        ))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('approval_status')
