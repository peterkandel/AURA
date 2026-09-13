"""add opportunity table

Revision ID: 9d5e9b8f2c3d
Revises: 301cdab76e6a
Create Date: 2026-09-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d5e9b8f2c3d'
down_revision = '301cdab76e6a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'opportunities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=80), nullable=False, server_default='General'),
        sa.Column('interests', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('difficulty', sa.String(length=32), nullable=False, server_default='Beginner'),
        sa.Column('estimated_hours_per_week', sa.Integer(), nullable=False, server_default='4'),
        sa.Column('required_skills', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_opportunities_status'), ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_opportunities_status'))
    op.drop_table('opportunities')
