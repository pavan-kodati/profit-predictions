"""track record v3: size groups, reasons, user trades, rejections

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13 12:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _trade_columns() -> list[sa.Column]:
    return [
        sa.Column('entry_date', sa.Date(), nullable=True),
        sa.Column('entry_price', sa.Float(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('buy_value', sa.Float(), nullable=False),
        sa.Column('stop_price', sa.Float(), nullable=False),
        sa.Column('hold_days', sa.Integer(), nullable=False),
        sa.Column('exit_date', sa.Date(), nullable=True),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('exit_reason', sa.String(length=16), nullable=True),
        sa.Column('charges', sa.Float(), nullable=True),
        sa.Column('tax', sa.Float(), nullable=True),
        sa.Column('net_pnl', sa.Float(), nullable=True),
        sa.Column('reason_key', sa.String(length=16), nullable=True),
        sa.Column('reason_text', sa.Text(), nullable=True),
        sa.Column('reason_links', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table('stocks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cap_group', sa.String(length=8), nullable=True))
    with op.batch_alter_table('practice_trades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reason_key', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('reason_text', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('reason_links', sa.JSON(), nullable=True))

    op.create_table(
        'user_trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('pick_type', sa.String(length=16), nullable=False),
        sa.Column('rating', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=8), nullable=False),
        sa.Column('skip_reason', sa.Text(), nullable=True),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('sell_requested_at', sa.DateTime(), nullable=True),
        *_trade_columns(),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_user_trades_symbol', 'user_trades', ['symbol'], unique=False)

    op.create_table(
        'rejections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('reject_date', sa.Date(), nullable=False),
        sa.Column('ref_close', sa.Float(), nullable=False),
        sa.Column('ref_date', sa.Date(), nullable=False),
        sa.Column('rating', sa.Float(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('ret', sa.Float(), nullable=True),
        sa.Column('index_ret', sa.Float(), nullable=True),
        sa.Column('days_tracked', sa.Integer(), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=True),
        sa.Column('final', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['report_id'], ['reports.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('report_id'),
    )
    op.create_index('ix_rejections_symbol', 'rejections', ['symbol'], unique=False)
    op.create_index('ix_rejections_reject_date', 'rejections', ['reject_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rejections_reject_date', table_name='rejections')
    op.drop_index('ix_rejections_symbol', table_name='rejections')
    op.drop_table('rejections')
    op.drop_index('ix_user_trades_symbol', table_name='user_trades')
    op.drop_table('user_trades')
    with op.batch_alter_table('practice_trades', schema=None) as batch_op:
        batch_op.drop_column('reason_links')
        batch_op.drop_column('reason_text')
        batch_op.drop_column('reason_key')
    with op.batch_alter_table('stocks', schema=None) as batch_op:
        batch_op.drop_column('cap_group')
