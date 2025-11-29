"""merge heads: pay_cycle fields + prior branch

Revision ID: 81cd6ca192cd
Revises: 9aa44702fd95, cc3f2a6a1b22
Create Date: 2025-11-02 09:53:07.422561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '81cd6ca192cd'
down_revision: Union[str, Sequence[str], None] = ('9aa44702fd95', 'cc3f2a6a1b22')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
