"""Motor y sesión async de SQLAlchemy hacia PostgreSQL."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# pool_size/max_overflow explícitos (los defaults de SQLAlchemy son 5+10=15
# conexiones concurrentes como máximo): cada mensaje procesado mantiene UNA
# sesión abierta durante toda su duración (ver conversation.py), así que con
# varios usuarios escribiendo al tiempo el default se queda corto. 20+20=40
# da margen cómodo sin acercarse al max_connections por defecto de Postgres.
engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=20, max_overflow=20)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Crea las tablas si no existen. Suficiente para una v1 local;
    si el proyecto crece, migrar a Alembic para versionar el esquema."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
