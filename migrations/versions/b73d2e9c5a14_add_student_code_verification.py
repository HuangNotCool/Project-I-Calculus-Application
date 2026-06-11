"""add student code verification

Revision ID: b73d2e9c5a14
Revises: a62c1a8d4f90
Create Date: 2026-06-11

"""
from alembic import op
import sqlalchemy as sa


revision = 'b73d2e9c5a14'
down_revision = 'a62c1a8d4f90'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'student_code_verified',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        ))
        batch_op.add_column(sa.Column('student_code_verified_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('student_code_verified_by', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_user_student_code_verified_by',
            'user',
            ['student_code_verified_by'],
            ['id']
        )


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint('fk_user_student_code_verified_by', type_='foreignkey')
        batch_op.drop_column('student_code_verified_by')
        batch_op.drop_column('student_code_verified_at')
        batch_op.drop_column('student_code_verified')
