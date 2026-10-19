"""Memoria corta de conversación por número de teléfono.

Equivale al nodo "Simple Memory3" (memoryBufferWindow) del flujo de n8n. Ahí
la sesión se identificaba con `instancia_ws + telefono` porque Evolution API
puede manejar varias instancias de WhatsApp a la vez; con WhatsApp Cloud API
sólo hay un número de negocio por despliegue, así que basta con el teléfono.
"""
from __future__ import annotations

import json

from redis.asyncio import Redis

from app.config import get_settings

settings = get_settings()


def _key(phone: str) -> str:
    return f"mercoia:memoria:{phone}"


async def get_history(redis: Redis, phone: str) -> list[dict]:
    raw = await redis.lrange(_key(phone), 0, -1)
    return [json.loads(item) for item in raw]


async def append_turn(redis: Redis, phone: str, role: str, content: str) -> None:
    key = _key(phone)
    await redis.rpush(key, json.dumps({"role": role, "content": content}))
    await redis.ltrim(key, -settings.memory_window * 2, -1)
    await redis.expire(key, settings.memory_ttl_seconds)
