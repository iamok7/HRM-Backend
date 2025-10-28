"""add pay_cycle_name to pay_runs

Revision ID: 1b2c3d4e5f6a
Revises: 8c2a1f3a3c2f
Create Date: 2025-10-26 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b2c3d4e5f6a'
down_revision: Union[str, Sequence[str], None] = '8c2a1f3a3c2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pay_runs', sa.Column('pay_cycle_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('pay_runs', 'pay_cycle_name')

