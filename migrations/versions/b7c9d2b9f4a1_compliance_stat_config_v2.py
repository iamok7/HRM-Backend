"""compliance stat_config v2 (scope+priority+indexes)

Revision ID: b7c9d2b9f4a1
Revises: 640b20171af8
Create Date: 2025-11-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c9d2b9f4a1'
down_revision: Union[str, Sequence[str], None] = '640b20171af8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns (keep legacy ones intact)
    # type enum
    stat_type = sa.Enum('PF', 'ESI', 'PT', 'LWF', name='statconfig_type')
    try:
        stat_type.create(op.get_bind(), checkfirst=True)
    except Exception:
        # Some backends (sqlite) or re-runs may not need explicit type create
        pass

    op.add_column('stat_configs', sa.Column('type', stat_type, nullable=True))
    op.add_column('stat_configs', sa.Column('scope_company_id', sa.Integer(), nullable=True))
    op.add_column('stat_configs', sa.Column('scope_state', sa.String(length=10), nullable=True))
    op.add_column('stat_configs', sa.Column('priority', sa.Integer(), server_default='100', nullable=False))
    op.add_column('stat_configs', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('stat_configs', sa.Column('closed_by', sa.Integer(), nullable=True))
    op.add_column('stat_configs', sa.Column('closed_at', sa.DateTime(), nullable=True))

    # New composite index for resolution
    op.create_index('ix_statcfg_resolve', 'stat_configs', ['type', 'scope_state', 'scope_company_id', 'effective_from', 'effective_to', 'priority'], unique=False)
    # Additional overlap/index to support scope/company/state keyed lookups
    try:
        op.create_index('ix_statcfg_active_window', 'stat_configs', ['type','scope','company_id','state','key','effective_from','effective_to'], unique=False)
    except Exception:
        pass


def downgrade() -> None:
    # Drop composite index
    try:
        op.drop_index('ix_statcfg_resolve', table_name='stat_configs')
    except Exception:
        pass
    try:
        op.drop_index('ix_statcfg_active_window', table_name='stat_configs')
    except Exception:
        pass

    # Drop added columns
    for col in ('closed_at', 'closed_by', 'created_by', 'priority', 'scope_state', 'scope_company_id', 'type'):
        try:
            op.drop_column('stat_configs', col)
        except Exception:
            pass

    # Drop enum type if present
    try:
        stat_type = sa.Enum('PF', 'ESI', 'PT', 'LWF', name='statconfig_type')
        stat_type.drop(op.get_bind(), checkfirst=True)
    except Exception:
        pass
