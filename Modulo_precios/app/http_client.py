"""Cliente httpx compartido en toda la app (pool de conexiones reutilizado
tanto para descargar boletines del DANE como para llamar a la Graph API)."""
from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": "MercoIA/1.0 (+https://github.com/)"},
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
