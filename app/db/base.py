from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models.

    Alembic reads `Base.metadata` to diff the code against the database, so
    every model module must be imported before autogenerate runs — see the
    import block in `alembic/env.py`.
    """
