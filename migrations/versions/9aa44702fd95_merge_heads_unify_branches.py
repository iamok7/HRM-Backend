"""merge heads (unify branches)

Revision ID: 9aa44702fd95
Revises: 1b2c3d4e5f6a, b7c9d2b9f4a1
Create Date: 2025-11-01 16:42:39.603227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9aa44702fd95'
down_revision: Union[str, Sequence[str], None] = ('1b2c3d4e5f6a', 'b7c9d2b9f4a1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
