"""Cliente delgado sobre la WhatsApp Cloud API (Meta Graph API).

Reemplaza al nodo "Send Text" (n8n-nodes-evolution-api) del flujo original.
Sólo necesita el access token y el phone_number_id que se obtienen en
developers.facebook.com > tu App > WhatsApp > API Setup.

Desde junio de 2026, un usuario puede escribirle a un negocio con su nombre
de usuario de WhatsApp en vez de su número real; en ese caso el mensaje trae
un BSUID (Business-Scoped User ID, ej. "CO.1541771343931331") en vez de un
wa_id normal (ver webhook_parser.py). Para responderle, la Graph API exige
un campo distinto en el payload: "recipient" (no "to") -- confirmado con la
documentación oficial de Meta tras ver que responder con "to" a un BSUID
producía el error 131026 "Message undeliverable" en producción (9-sep-2026).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _es_bsuid(identificador: str) -> bool:
    """True si `identificador` es un BSUID (ver docstring del módulo) en vez
    de un wa_id normal. Los BSUID tienen forma "<código país>.<id>" (ej.
    "CO.1541771343931331", "US.13491208655302741918"); un wa_id real es
    solo dígitos, sin punto."""
    return "." in identificador


class WhatsAppClient:
    def __init__(self, http_client: httpx.AsyncClient):
        self._http = http_client

    @property
    def _url(self) -> str:
        return f"{settings.graph_api_base}/{settings.whatsapp_phone_number_id}/messages"

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

    async def send_text(self, to: str, body: str, *, delay: float | None = None) -> None:
        """Envía un mensaje de texto. `delay` simula el "options_message.delay"
        que tenían los nodos Send Text en n8n, para que la conversación no se
        sienta instantánea/robótica."""
        if delay is None:
            delay = settings.send_message_delay_seconds
        if delay:
            await asyncio.sleep(delay)

        payload = {
            "messaging_product": "whatsapp",
            "type": "text",
            "text": {"body": body, "preview_url": False},
        }
        if _es_bsuid(to):
            payload["recipient_type"] = "individual"
            payload["recipient"] = to
        else:
            payload["to"] = to

        try:
            resp = await self._http.post(self._url, json=payload, headers=self._headers, timeout=15.0)
            if resp.status_code >= 400:
                logger.error("Error enviando WhatsApp a %s: %s %s", to, resp.status_code, resp.text)
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Fallo de red enviando mensaje de WhatsApp a %s", to)

    async def mark_as_read(self, message_id: str) -> None:
        payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        try:
            await self._http.post(self._url, json=payload, headers=self._headers, timeout=10.0)
        except httpx.HTTPError:
            logger.warning("No se pudo marcar como leído el mensaje %s", message_id)
