"""initial_schema - complete table creation

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-11 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001_initial'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables from scratch."""
    
    # Users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(50), unique=True, index=True, nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('avatar_url', sa.Text(), nullable=False, server_default='/logo.png'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        if_not_exists=True,
    )

    # System settings table
    op.create_table(
        'system_settings',
        sa.Column('key', sa.String(50), primary_key=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('description', sa.String(255), nullable=True),
        if_not_exists=True,
    )

    # Pending links table
    op.create_table(
        'pending_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('share_url', sa.String(255), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='auditing'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_check', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        if_not_exists=True,
    )

    # Link history table
    op.create_table(
        'link_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('original_url', sa.String(255), unique=True, index=True, nullable=False),
        sa.Column('share_link', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        if_not_exists=True,
    )

    # Excel tasks table
    op.create_table(
        'excel_tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='wait'),
        sa.Column('total_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('success_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('target_dir', sa.String(255), nullable=True),
        sa.Column('interval_min', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('interval_max', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('skip_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_row', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_waiting', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        if_not_exists=True,
    )

    # Excel task items table
    op.create_table(
        'excel_task_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.Integer(), index=True, nullable=False),
        sa.Column('row_index', sa.Integer(), nullable=False),
        sa.Column('original_url', sa.String(255), nullable=False),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('extraction_code', sa.String(50), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='待处理'),
        sa.Column('new_share_url', sa.String(255), nullable=True),
        sa.Column('error_msg', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table('excel_task_items')
    op.drop_table('excel_tasks')
    op.drop_table('link_history')
    op.drop_table('pending_links')
    op.drop_table('system_settings')
    op.drop_table('users')
