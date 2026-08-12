"""enable pgvector extension

Revision ID: 0001
Revises:
Create Date: 2026-08-09

The extension has to exist before any table can declare a vector column, so
it gets its own migration ahead of the schema. Creating it in a migration
rather than by hand means a fresh database is reproducible from `upgrade head`
alone — nobody has to remember a manual step.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # Left in place deliberately: dropping the extension would cascade into
    # every vector column that depends on it.
    pass
