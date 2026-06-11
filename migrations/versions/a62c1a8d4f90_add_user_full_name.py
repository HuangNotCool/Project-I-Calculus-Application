"""add user full name

Revision ID: a62c1a8d4f90
Revises: f1ed8e8eb7ed
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa


revision = 'a62c1a8d4f90'
down_revision = 'f1ed8e8eb7ed'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('full_name', sa.String(length=150), nullable=True))

    op.execute("UPDATE user SET full_name = username WHERE full_name IS NULL OR TRIM(full_name) = ''")

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'full_name',
            existing_type=sa.String(length=150),
            nullable=False
        )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('full_name')
