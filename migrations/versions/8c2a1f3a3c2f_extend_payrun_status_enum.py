"""extend payrun_status_enum with calculated, approved

Revision ID: 8c2a1f3a3c2f
Revises: 47054cc45d49
Create Date: 2025-10-25 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c2a1f3a3c2f'
down_revision: Union[str, Sequence[str], None] = '47054cc45d49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add new allowed values to the Postgres ENUM backing pay_runs.status.
    For PostgreSQL, ALTER TYPE ... ADD VALUE IF NOT EXISTS is safe and idempotent.
    For other dialects (e.g., SQLite), this is a no-op.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == 'postgresql':
        # Add values if they are missing
        op.execute("ALTER TYPE payrun_status_enum ADD VALUE IF NOT EXISTS 'calculated';")
        op.execute("ALTER TYPE payrun_status_enum ADD VALUE IF NOT EXISTS 'approved';")
        # 'locked' already existed in the original enum; 'posted' remains for compatibility
    else:
        # No action for non-Postgres; SQLAlchemy Enum on SQLite typically uses CHECK constraints
        # and would require table rebuild. Skip to keep migration simple in dev setups.
        pass


def downgrade() -> None:
    """No downgrade for ENUM value removal.
    Removing values from a PostgreSQL ENUM requires type recreate + table rewrite,
    which is intentionally omitted here.
    """
    pass

