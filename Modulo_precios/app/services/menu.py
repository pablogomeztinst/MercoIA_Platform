"""Menú inicial: la primera vez que un teléfono escribe, se le pregunta si
quiere 1) consultar precios o 2) el módulo de detección de enfermedades.

El módulo de precios es el flujo que ya existe (ver conversation.py); el de
enfermedades todavía está en desarrollo, así que su opción solo confirma que
llegará pronto y deja al usuario en el flujo de precios.

"Primera vez" se decide consultando si el teléfono ya tiene algún mensaje en
conversation_logs (no hace falta una tabla nueva). Mientras se espera la
respuesta al menú se guarda un estado corto en Redis (mismo patrón que
ambiguous.py/suggestions.py) para interpretar el siguiente mensaje como la
elección; si el usuario ignora el menú y escribe directo el nombre de un
producto, igual se le deja seguir por el flujo normal de precios en vez de
insistir con el menú.
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ConversationLog
from app.services.classifier import MENSAJE_CONTINUAR
from app.services.text_utils import normalize_basic

settings = get_settings()

MENSAJE_MENU_INICIAL = (
    "👋 Hola, soy la asistente de *MercoIA*. 🌱\n\n"
    "Tengo dos servicios disponibles:\n\n"
    "1️⃣ *Consultar precios agrícolas* — precios mayoristas actualizados con base "
    "en información oficial de SIPSA (DANE). 📊\n"
    "2️⃣ *Detección de enfermedades* — análisis de fotos de cultivos. 🌿🔍\n\n"
    "Responde *1* o *2* para continuar. 🙂"
)

MENSAJE_ENFERMEDADES_NO_DISPONIBLE = (
    "🚧 El servicio de *detección de enfermedades* todavía está en desarrollo "
    "y por ahora no está disponible.\n\n"
    "En pocos días estará listo para ti. Mientras tanto, puedo ayudarte con la "
    "consulta de precios agrícolas.\n\n"
    "Escríbeme el nombre del producto que deseas consultar. 🙂"
)

# Confirmación de la opción 1: es literalmente el mismo mensaje que ya se usa
# para "continuar consultando" (ver classifier.py), no hace falta duplicarlo.
MENSAJE_PRECIOS_CONFIRMACION = MENSAJE_CONTINUAR

_OPCION_PRECIOS = {
    "1", "uno", "precio", "precios", "consultar precio", "consultar precios",
    "modulo de precios", "modulo precios", "opcion 1",
}
_OPCION_ENFERMEDADES = {
    "2", "dos", "enfermedad", "enfermedades", "plaga", "plagas",
    "deteccion", "deteccion de enfermedades", "diagnostico",
    "diagnostico de enfermedades", "sanidad", "opcion 2",
}


def _redis_key(phone: str) -> str:
    return f"mercoia:menu_pendiente:{phone}"


async def es_primer_contacto(db: AsyncSession, phone: str) -> bool:
    """True si este teléfono nunca ha escrito antes (no hay ningún registro
    en conversation_logs). Debe llamarse ANTES de loguear el mensaje entrante
    del turno actual."""
    result = await db.execute(select(ConversationLog.id).where(ConversationLog.phone == phone).limit(1))
    return result.scalar_one_or_none() is None


async def marcar_menu_pendiente(redis: Redis, phone: str) -> None:
    await redis.set(_redis_key(phone), "1", ex=settings.menu_pendiente_ttl_seconds)


@dataclass
class ResolucionMenu:
    # "enfermedades"       -> eligió el módulo aún no disponible.
    # "precios_confirmado" -> eligió explícitamente precios (ej. "1"); no es
    #                         un nombre de producto, solo hay que confirmar.
    # "precios_implicito"  -> ignoró el menú y escribió otra cosa (ej. ya
    #                         escribió el producto); se deja seguir tal cual
    #                         por el flujo normal de precios.
    # "no_aplica"          -> no había ningún menú pendiente para este turno.
    tipo: str
    mensaje_usuario: str


async def resolver_pendiente(redis: Redis, phone: str, mensaje_usuario: str) -> ResolucionMenu:
    """Si había un menú pendiente de responder, interpreta la elección."""
    key = _redis_key(phone)
    habia_pendiente = await redis.get(key)
    if not habia_pendiente:
        return ResolucionMenu("no_aplica", mensaje_usuario)

    await redis.delete(key)
    mensaje = normalize_basic(mensaje_usuario)

    if mensaje in _OPCION_ENFERMEDADES:
        return ResolucionMenu("enfermedades", mensaje_usuario)

    if mensaje in _OPCION_PRECIOS:
        return ResolucionMenu("precios_confirmado", mensaje_usuario)

    # No coincidió con ninguna opción explícita: probablemente escribió
    # directo el nombre de un producto, ignorando el menú. Se asume el
    # módulo de precios implícitamente en vez de insistir con el menú.
    return ResolucionMenu("precios_implicito", mensaje_usuario)
