"""Persist character reference image metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_images",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("work_id", sa.String(), sa.ForeignKey("works.id"), nullable=False),
        sa.Column("character_id", sa.String(), nullable=False),
        sa.Column("asset_id", sa.String(), nullable=False, unique=True),
        sa.Column("costume_version", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("work_id", "character_id", "asset_id"),
    )


def downgrade() -> None:
    op.drop_table("character_images")
