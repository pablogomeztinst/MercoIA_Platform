"""Endpoints del webhook de WhatsApp Cloud API.

GET  -> verificación del webhook que hace Meta al configurarlo en el panel.
POST -> eventos entrantes (mensajes de usuarios, confirmaciones de estado).

Meta exige que el webhook responda rápido (ideal < unos pocos segundos), así
que aquí sólo se valida/despacha el trabajo pesado (bajar Excel del DANE,
llamar al LLM, etc.) como tarea en segundo plano; la respuesta HTTP se
devuelve de inmediato.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.db import SessionLocal
from app.http_client import get_http_client
from app.redis_client import redis_client
from app.services import conversation, dedup
from app.services.webhook_parser import extract_incoming_messages
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook/whatsapp", tags=["webhook"])
settings = get_settings()

if not settings.whatsapp_app_secret:
    logger.warning(
        "WHATSAPP_APP_SECRET no está configurado: no se podrá verificar la firma "
        "de los webhooks entrantes. Configúralo antes de salir a producción."
    )


@router.get("")
async def verificar_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook de WhatsApp verificado correctamente por Meta.")
        return PlainTextResponse(hub_challenge or "")
    logger.warning("Intento de verificación de webhook con token inválido.")
    raise HTTPException(status_code=403, detail="Verify token inválido")


def _firma_valida(cuerpo_crudo: bytes, firma_header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        return True  # sin app secret configurado, no se puede validar (solo dev)
    if not firma_header or not firma_header.startswith("sha256="):
        return False

    firma_esperada = hmac.new(
        settings.whatsapp_app_secret.encode(), cuerpo_crudo, hashlib.sha256
    ).hexdigest()
    firma_recibida = firma_header.removeprefix("sha256=")
    return hmac.compare_digest(firma_esperada, firma_recibida)


async def _procesar_en_segundo_plano(phone: str, mensaje: str) -> None:
    async with SessionLocal() as db:
        await conversation.procesar_mensaje(
            phone=phone, mensaje_usuario=mensaje, redis=redis_client, db=db, http_client=get_http_client()
        )


async def _marcar_leido_en_segundo_plano(message_id: str) -> None:
    await WhatsAppClient(get_http_client()).mark_as_read(message_id)


async def _notificar_tipo_no_soportado(phone: str) -> None:
    await WhatsAppClient(get_http_client()).send_text(phone, conversation.MENSAJE_TIPO_NO_SOPORTADO)


@router.post("")
async def recibir_webhook(request: Request, background_tasks: BackgroundTasks, x_hub_signature_256: str | None = Header(None)) -> dict:
    cuerpo_crudo = await request.body()

    if not _firma_valida(cuerpo_crudo, x_hub_signature_256):
        logger.warning("Webhook rechazado: firma X-Hub-Signature-256 inválida.")
        raise HTTPException(status_code=401, detail="Firma inválida")

    payload = await request.json()
    mensajes = extract_incoming_messages(payload)

    async with SessionLocal() as db:
        for msg in mensajes:
            if not msg.phone or not msg.message_id:
                continue

            duplicado = await dedup.ya_procesado(redis_client, db, msg.message_id, msg.phone)
            if duplicado:
                logger.info("Mensaje %s ya procesado antes, se ignora reintento.", msg.message_id)
                continue

            background_tasks.add_task(_marcar_leido_en_segundo_plano, msg.message_id)

            if msg.message_type != "text" or not msg.text:
                logger.info("Mensaje %s de tipo no soportado (%s), se avisa al usuario.", msg.message_id, msg.message_type)
                background_tasks.add_task(_notificar_tipo_no_soportado, msg.phone)
                continue

            background_tasks.add_task(_procesar_en_segundo_plano, msg.phone, msg.text)

    return {"status": "received"}
