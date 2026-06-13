"""bind chat sessions to a compliance run + guideline

Adds nullable chat_sessions.subject_run_id and chat_sessions.guideline_id so a
chat session can be grounded in a specific audit run and guideline. Idempotent.

Revision ID: d4e1b6c8f3a2
Revises: c3d9a7b2e1f4
Create Date: 2026-05-31 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e1b6c8f3a2"
down_revision: Union[str, None] = "c3d9a7b2e1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols() -> set[str]:
    insp = sa.inspect(op.get_bind())
    if "chat_sessions" not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns("chat_sessions")}


def upgrade() -> None:
    cols = _cols()
    if cols:  # table exists
        if "subject_run_id" not in cols:
            op.add_column("chat_sessions", sa.Column("subject_run_id", sa.Uuid(), nullable=True))
        if "guideline_id" not in cols:
            op.add_column("chat_sessions", sa.Column("guideline_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    cols = _cols()
    if "guideline_id" in cols:
        op.drop_column("chat_sessions", "guideline_id")
    if "subject_run_id" in cols:
        op.drop_column("chat_sessions", "subject_run_id")
