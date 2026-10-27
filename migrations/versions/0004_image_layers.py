"""Separate character, environment and composition images with reference lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shot_images", sa.Column("kind", sa.String(), nullable=False, server_default="composition")
    )
    op.add_column(
        "shot_images", sa.Column("subject_id", sa.String(), nullable=False, server_default="")
    )
    op.add_column(
        "shot_images", sa.Column("reference_assets", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    op.drop_column("shot_images", "reference_assets")
    op.drop_column("shot_images", "subject_id")
    op.drop_column("shot_images", "kind")
