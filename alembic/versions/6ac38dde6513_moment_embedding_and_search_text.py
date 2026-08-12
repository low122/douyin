"""moment embedding and search_text

Revision ID: 6ac38dde6513
Revises: 235df0a0d561
Create Date: 2026-08-11

Hand-finished after autogenerate, which got three things wrong here:
it referenced pgvector's type without importing it (a NameError on first run),
and it emitted neither index — an expression index and a vector index are both
invisible to model introspection. Without them each half of hybrid search is a
sequential scan.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "6ac38dde6513"
down_revision: str | None = "235df0a0d561"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "moment",
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536), nullable=True),
    )
    op.add_column("moment", sa.Column("search_text", sa.Text(), nullable=True))

    # Full-text half. 'simple' rather than a language configuration because the
    # text arriving here is already segmented by jieba — Postgres only has to
    # split on spaces, and any stemmer would be wrong for Chinese anyway.
    op.execute(
        "CREATE INDEX ix_moment_search_tsv ON moment "
        "USING GIN (to_tsvector('simple', search_text))"
    )

    # Vector half. HNSW rather than IVFFlat: IVFFlat needs to be built against
    # existing rows to pick its lists, which is awkward for a store that starts
    # empty and grows one video at a time.
    op.execute(
        "CREATE INDEX ix_moment_embedding_hnsw ON moment "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_moment_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_moment_search_tsv")
    op.drop_column("moment", "search_text")
    op.drop_column("moment", "embedding")
