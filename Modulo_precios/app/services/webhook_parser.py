"""Parseo del payload que envía WhatsApp Cloud API a nuestro webhook.

Formato oficial (resumido):
    {
      "object": "whatsapp_business_account",
      "entry": [{
        "changes": [{
          "field": "messages",
          "value": {
            "metadata": {"phone_number_id": "..."},
            "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
            "messages": [{"from": "...", "id": "...", "type": "text", "text": {"body": "..."}}],
            "statuses": [...]   # confirmaciones de entrega/lectura de mensajes SALIENTES,
                                 # no son mensajes de usuario: se ignoran.
          }
        }]
      }]
    }

Formato alterno (observado en producción, sep-2026): usuarios que le escriben
al negocio sin compartir su número real (identidad de usuario de Meta en vez
de número de teléfono) traen "from_user_id"/"user_id" en vez de
"from"/"wa_id" -- sin este número real, la conversación se identifica con ese
identificador opaco (ej. "CO.1541771343931331"), que funciona igual como
clave de conversación y como destinatario al responder (WhatsAppClient.send_text
solo necesita "algo" que la Graph API acepte en "to", no específicamente un
número). Antes de este fix, `msg.get("from", "")` daba cadena vacía para
estos mensajes y el webhook los descartaba en silencio (nunca se marcaban
como leídos ni se respondían) -- ver conversación con el usuario del 9-sep-2026.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IncomingMessage:
    phone: str  # identificador del remitente: wa_id (número) o, si no lo comparte, su user_id opaco
    message_id: str
    message_type: str  # "text" | "image" | "audio" | "document" | ...
    text: str | None
    profile_name: str | None
    timestamp: str | None


def extract_incoming_messages(payload: dict) -> list[IncomingMessage]:
    resultados: list[IncomingMessage] = []

    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            mensajes = value.get("messages")
            if not mensajes:
                continue  # ej. era un "statuses" (delivered/read), no un mensaje de usuario

            contactos = {}
            for c in value.get("contacts", []) or []:
                if c.get("wa_id"):
                    contactos[c["wa_id"]] = c
                if c.get("user_id"):
                    contactos[c["user_id"]] = c

            for msg in mensajes:
                # "from" = número real (caso normal); "from_user_id" = caso
                # sin número compartido (ver docstring del módulo).
                phone = msg.get("from") or msg.get("from_user_id") or ""
                contacto = contactos.get(phone, {})
                profile_name = (contacto.get("profile") or {}).get("name")

                tipo = msg.get("type", "unknown")
                texto = None
                if tipo == "text":
                    texto = (msg.get("text") or {}).get("body")
                elif tipo == "button":
                    texto = (msg.get("button") or {}).get("text")
                elif tipo == "interactive":
                    interactive = msg.get("interactive") or {}
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    texto = reply.get("title")

                resultados.append(
                    IncomingMessage(
                        phone=phone,
                        message_id=msg.get("id", ""),
                        message_type=tipo,
                        text=texto,
                        profile_name=profile_name,
                        timestamp=msg.get("timestamp"),
                    )
                )

    return resultados
