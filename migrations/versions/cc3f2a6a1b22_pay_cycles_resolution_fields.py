"""add resolution fields to pay_cycles

Revision ID: cc3f2a6a1b22
Revises: b7c9d2b9f4a1
Create Date: 2025-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc3f2a6a1b22'
down_revision: Union[str, Sequence[str], None] = 'b7c9d2b9f4a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pay_cycles', sa.Column('effective_from', sa.Date(), nullable=True))
    op.add_column('pay_cycles', sa.Column('effective_to', sa.Date(), nullable=True))
    op.add_column('pay_cycles', sa.Column('priority', sa.Integer(), nullable=False, server_default='100'))
    op.create_index('ix_pay_cycles_resolve', 'pay_cycles', ['company_id', 'active', 'effective_from', 'effective_to', 'priority'], unique=False)


def downgrade() -> None:
    try:
        op.drop_index('ix_pay_cycles_resolve', table_name='pay_cycles')
    except Exception:
        pass
    for col in ('priority','effective_to','effective_from'):
        try:
            op.drop_column('pay_cycles', col)
        except Exception:
            pass

