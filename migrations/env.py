from alembic import context

from novel_anime.persistence.database import Base, make_engine

with make_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
