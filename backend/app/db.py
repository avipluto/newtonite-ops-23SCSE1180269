"""Database engine and per-request session."""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app import config

engine = create_engine(config.DATABASE_URL, pool_size=10, max_overflow=10, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one session (= one transaction) per request.

    Routes call db.commit() exactly once when everything succeeded. If anything
    raises before that, close() rolls the whole transaction back.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
