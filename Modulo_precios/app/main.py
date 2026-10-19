from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import SessionLocal, init_models
from app.http_client import close_http_client, get_http_client
from app.logging_config import setup_logging
from app.redis_client import redis_client
from app.routers import health, webhook
from app.services import sipsa_fetch

setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


async def _precalentar_periodicamente() -> None:
    """Refresca sola la caché del boletín SIPSA más reciente cada
    `sipsa_precalentar_intervalo_segundos`, para que ningún usuario real sea
    el primero en pagar el costo de una caché fría o una caída momentánea de
    Pentaho (ver sipsa_fetch.precalentar)."""
    http_client = get_http_client()
    while True:
        async with SessionLocal() as db:
            await sipsa_fetch.precalentar(redis=redis_client, db=db, http_client=http_client)
        await asyncio.sleep(settings.sipsa_precalentar_intervalo_segundos)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando MercoIA...")
    get_http_client()  # calienta el pool de conexiones httpx
    await init_models()
    tarea_precalentamiento = asyncio.create_task(_precalentar_periodicamente())
    logger.info("MercoIA listo.")
    yield
    logger.info("Cerrando MercoIA...")
    tarea_precalentamiento.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await tarea_precalentamiento
    await close_http_client()
    await redis_client.aclose()


app = FastAPI(
    title="MercoIA — Módulo de precios",
    description=(
        "Asistente de WhatsApp que consulta precios mayoristas de productos "
        "agrícolas publicados diariamente por el DANE (SIPSA)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(webhook.router)


@app.get("/")
async def root() -> dict:
    return {"service": "MercoIA", "status": "running"}
