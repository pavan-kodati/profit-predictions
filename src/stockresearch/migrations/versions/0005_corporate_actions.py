"""splits and bonus issues: corporate actions, trade corrections, price basis day (plan v5)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Yahoo history was first loaded on this day; Yahoo prices are split-adjusted as of the day they are downloaded.
FIRST_LOAD = '2026-11-12'


def upgrade() -> None:
    with op.batch_alter_table('daily_prices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('basis_date', sa.Date(), nullable=True))
    op.execute("UPDATE daily_prices SET basis_date = date WHERE source = 'nse'")
    op.execute(sa.text("UPDATE daily_prices SET basis_date = CASE WHEN date > :first THEN date ELSE :first END "
                       "WHERE source != 'nse'").bindparams(first=FIRST_LOAD))

    op.create_table(
        'corporate_actions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('ex_date', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('factor', sa.Float(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('symbol', 'ex_date', 'kind', 'source'),
    )
    op.create_index('ix_corporate_actions_symbol', 'corporate_actions', ['symbol'], unique=False)

    op.create_table(
        'trade_corrections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('book', sa.String(length=8), nullable=False),
        sa.Column('trade_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('action_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['action_id'], ['corporate_actions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('book', 'trade_id'),
    )


def downgrade() -> None:
    op.drop_table('trade_corrections')
    op.drop_index('ix_corporate_actions_symbol', table_name='corporate_actions')
    op.drop_table('corporate_actions')
    with op.batch_alter_table('daily_prices', schema=None) as batch_op:
        batch_op.drop_column('basis_date')
