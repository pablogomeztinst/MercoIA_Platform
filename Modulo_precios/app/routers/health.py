from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.redis_client import redis_client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_session)) -> dict:
    estado = {"status": "ok", "postgres": "ok", "redis": "ok"}

    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.exception("Healthcheck: PostgreSQL no disponible")
        estado["postgres"] = "error"
        estado["status"] = "degraded"

    try:
        await redis_client.ping()
    except Exception:  # noqa: BLE001
        logger.exception("Healthcheck: Redis no disponible")
        estado["redis"] = "error"
        estado["status"] = "degraded"

    return estado
