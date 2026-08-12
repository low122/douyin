"""One live set of moments per video

Narrows the moment unique key from (video_id, schema_version, model, start_sec)
to (video_id, start_sec), and deletes everything a video carries from an older
schema version.

The wide key existed so two models could be extracted side by side and compared,
which is how gpt-4o was chosen — see ADR-0008, where the numbers now live. It was
only ever half-built: extraction and embedding filtered on (schema_version,
model) while search filtered on schema_version alone, so an install that changed
EXTRACT_MODEL and re-ingested a video would have got every moment back twice
under near-identical titles.

This migration deletes data, on purpose, and cannot be undone: the superseded
moments are gone, not archived. Re-extracting a video restores it from the
transcript, which is a separate table and is not touched — so the cost of being
wrong here is one extraction call per video, not a re-transcription.

Revision ID: b41c7f2a0e93
Revises: 6ac38dde6513
"""

from alembic import op
import sqlalchemy as sa

revision = "b41c7f2a0e93"
down_revision = "6ac38dde6513"
branch_labels = None
depends_on = None

OLD_CONSTRAINT = "uq_moment_video_version_model_start"
NEW_CONSTRAINT = "uq_moment_video_start"


def upgrade() -> None:
    # Order matters. The new constraint cannot be created while superseded rows
    # are present — they collide on (video_id, start_sec), which is exactly the
    # duplication being removed — so the delete has to come first.
    #
    # "Superseded" is defined per video rather than globally: a video extracted
    # under an older schema that has never been re-run keeps what it has, because
    # deleting it would leave the video in the library with nothing to find and
    # no indication why.
    op.execute(
        sa.text(
            """
            DELETE FROM moment m
            WHERE EXISTS (
                SELECT 1 FROM moment newer
                WHERE newer.video_id = m.video_id
                  AND newer.schema_version > m.schema_version
            )
            """
        )
    )
    # Same video, same schema, two models: keep the one that was written last.
    # There is no quality signal available here, and recency is the only ordering
    # that matches how these arose — the later run is the one the operator asked
    # for when they changed the model.
    op.execute(
        sa.text(
            """
            DELETE FROM moment m
            USING moment other
            WHERE m.video_id = other.video_id
              AND m.start_sec = other.start_sec
              AND m.schema_version = other.schema_version
              AND m.model <> other.model
              AND (m.created_at, m.id) < (other.created_at, other.id)
            """
        )
    )

    op.drop_constraint(OLD_CONSTRAINT, "moment", type_="unique")
    op.create_unique_constraint(NEW_CONSTRAINT, "moment", ["video_id", "start_sec"])


def downgrade() -> None:
    # Restores the constraint only. The deleted rows are not recoverable, which
    # is what makes the upgrade one-way in practice whatever this says.
    op.drop_constraint(NEW_CONSTRAINT, "moment", type_="unique")
    op.create_unique_constraint(
        OLD_CONSTRAINT, "moment", ["video_id", "schema_version", "model", "start_sec"]
    )
