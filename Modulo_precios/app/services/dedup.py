"""Deduplicación de webhooks entrantes.

WhatsApp Cloud API puede reintentar la entrega del mismo evento si tu
servidor no respondió 200 a tiempo. Sin esto, un reintento produciría una
respuesta duplicada al agricultor."""
from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProcessedMessage

_DEDUP_TTL_SECONDS = 24 * 3600


async def ya_procesado(redis: Redis, db: AsyncSession, message_id: str, phone: str) -> bool:
    """True si el mensaje ya fue procesado antes. Si no lo estaba, lo marca
    como procesado atómicamente (Redis SETNX) y lo persiste en Postgres."""
    key = f"mercoia:msg:{message_id}"
    es_nuevo = await redis.set(key, "1", ex=_DEDUP_TTL_SECONDS, nx=True)
    if not es_nuevo:
        return True

    stmt = pg_insert(ProcessedMessage).values(message_id=message_id, phone=phone)
    stmt = stmt.on_conflict_do_nothing(index_elements=["message_id"])
    await db.execute(stmt)
    await db.commit()
    return False
