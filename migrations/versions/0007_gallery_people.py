"""Keep gallery person rows independently of collected images."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    people = op.create_table(
        "gallery_people",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("work_id", sa.String(), sa.ForeignKey("works.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("work_id", "name"),
    )
    op.create_index("ix_gallery_people_work_id", "gallery_people", ["work_id"])
    entries = sa.table(
        "library_entries", sa.column("work_id"), sa.column("kind"),
        sa.column("created_at"), sa.column("content", sa.JSON()),
    )
    connection = op.get_bind()
    seen = set()
    for row in connection.execute(
        sa.select(entries).where(entries.c.kind == "images").order_by(entries.c.created_at)
    ).mappings():
        content = row["content"]
        name = content.get("name", "").strip()
        key = (row["work_id"], name)
        if content.get("image_kind") == "character" and name and key not in seen:
            connection.execute(people.insert().values(
                id=uuid4().hex, work_id=row["work_id"], name=name, created_at=row["created_at"]
            ))
            seen.add(key)


def downgrade() -> None:
    op.drop_table("gallery_people")
