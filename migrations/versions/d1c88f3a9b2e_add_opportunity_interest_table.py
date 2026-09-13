"""add opportunity interest table

Revision ID: d1c88f3a9b2e
Revises: 9d5e9b8f2c3d
Create Date: 2026-09-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1c88f3a9b2e'
down_revision = '9d5e9b8f2c3d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'opportunity_interests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'opportunity_id', name='uq_opportunity_interest_user_opportunity'),
        sa.Index('ix_opportunity_interests_user_id', 'user_id'),
        sa.Index('ix_opportunity_interests_opportunity_id', 'opportunity_id'),
        sa.Index('ix_opportunity_interests_status', 'status'),
    )


def downgrade():
    op.drop_index(op.f('ix_opportunity_interests_status'), table_name='opportunity_interests')
    op.drop_index(op.f('ix_opportunity_interests_opportunity_id'), table_name='opportunity_interests')
    op.drop_index(op.f('ix_opportunity_interests_user_id'), table_name='opportunity_interests')
    op.drop_table('opportunity_interests')
