"""Última red de seguridad conversacional: cuando ni el modelo normalizador
ni el rescate automático por typo (ver normalizer.py) lograron identificar
el producto, pero sí hay algún producto real que es una variante ortográfica
razonable de lo que escribió el usuario, se le pregunta si se refería a eso
en vez de responder directamente "no existe".

Esto es distinto de ambiguous.py: allá el producto SÍ se identificó pero
tiene varias variedades reales (papa negra/criolla); acá el producto NO se
identificó con confianza, y se le muestran al usuario los candidatos más
cercanos por ortografía para que confirme. Es literalmente el último recurso
antes de decir que el alimento no está en la base de datos.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import get_settings
from app.services.text_utils import es_variante_razonable, limpiar_nombre_producto, similitud_maxima_palabras

settings = get_settings()

_TTL_SEGUNDOS = 5 * 60  # más corto que la desambiguación real (20 min): es una situación de menor certeza


def _redis_key(phone: str) -> str:
    return f"mercoia:sugerencia:{phone}"


def construir_mensaje_sugerencias(sugerencias: list[dict]) -> str:
    lineas = "\n".join(f"• {limpiar_nombre_producto(s['nombre'])}" for s in sugerencias)
    return (
        "🔎 No encontré exactamente ese producto, pero tengo registrado algo parecido:\n\n"
        f"{lineas}\n\n"
        "Si te refieres a alguno, respóndeme con su nombre. Si no es ninguno, "
        "escribe de nuevo el producto que buscas."
    )


async def guardar_contexto(redis: Redis, phone: str, mensaje_original: str, sugerencias: list[dict]) -> None:
    payload = {"mensaje_original": mensaje_original, "sugerencias": sugerencias}
    await redis.set(_redis_key(phone), json.dumps(payload), ex=_TTL_SEGUNDOS)


async def olvidar_pendiente(redis: Redis, phone: str) -> None:
    """Descarta un "¿quisiste decir...?" pendiente sin esperar respuesta (ej.
    si el usuario pidió volver al menú inicial a mitad de camino)."""
    await redis.delete(_redis_key(phone))


@dataclass
class ResolucionSugerencia:
    resuelto: bool
    mensaje_usuario: str


async def resolver_pendiente(redis: Redis, phone: str, mensaje_usuario: str) -> ResolucionSugerencia:
    """Si había sugerencias pendientes del turno anterior y este mensaje
    coincide con alguna, arma el mensaje efectivo con el nombre real de esa
    sugerencia (para que fluya de nuevo por el flujo normal de normalización
    y precios). Si no coincide con ninguna, borra el contexto y deja el
    mensaje tal cual, tratándolo como una consulta nueva."""
    key = _redis_key(phone)
    raw = await redis.get(key)
    if not raw:
        return ResolucionSugerencia(False, mensaje_usuario)

    try:
        contexto = json.loads(raw)
    except json.JSONDecodeError:
        await redis.delete(key)
        return ResolucionSugerencia(False, mensaje_usuario)

    await redis.delete(key)

    # Se exige la misma precisión que al construir las sugerencias
    # (distancia de edición, no solo similitud general) — si no, respuestas
    # sin ninguna relación real podían "confirmar" una sugerencia por pura
    # coincidencia de letras (ej. "zanahoria" se parecía por azar a "banano").
    mejor_nombre = None
    mejor_similitud = 0.0
    for sugerencia in contexto.get("sugerencias", []):
        if not es_variante_razonable(mensaje_usuario, sugerencia["nombre"]):
            continue
        similitud = similitud_maxima_palabras(mensaje_usuario, sugerencia["nombre"])
        if similitud > mejor_similitud:
            mejor_similitud = similitud
            mejor_nombre = sugerencia["nombre"]

    if mejor_nombre:
        return ResolucionSugerencia(True, limpiar_nombre_producto(mejor_nombre))

    return ResolucionSugerencia(False, mensaje_usuario)
