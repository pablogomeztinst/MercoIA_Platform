"""Cliente Redis compartido (caché de boletines SIPSA, contexto de ambigüedad,
memoria corta de conversación y deduplicación de webhooks)."""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.config import get_settings

_settings = get_settings()

redis_client: Redis = from_url(_settings.redis_url, decode_responses=True)
