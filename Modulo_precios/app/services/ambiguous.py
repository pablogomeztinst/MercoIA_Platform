"""Detección dinámica de productos con varias variedades/clasificaciones en
el boletín SIPSA del día (ej. "Papa negra" vs "Papa criolla", "Limón común"
vs "Limón Tahití", "Cebolla cabezona blanca" vs "Cebolla junca").

A diferencia de la primera versión (que tenía "papa" y "plátano" hardcodeados
copiando el flujo de n8n), esto se calcula en vivo contra la lista real de
productos del boletín vigente: agrupa por la primera palabra del nombre y,
si un grupo tiene 2+ variedades ese día, se le pregunta al usuario cuál
quiere en vez de dejar que el LLM adivine una. Así funciona igual para
cualquier producto que el DANE divida en variedades, sin tocar código — hoy
son papa/plátano/limón/cebolla, pero si el DANE agrega o quita una variedad
mañana, se detecta solo.

Nota de diseño: si dentro de un grupo alguna variedad es el nombre "pelado"
sin ningún calificativo (ej. "Tomate*" junto a "Tomate de árbol"), NO se
trata como ambiguo — se asume que el nombre pelado es la opción por defecto
cuando el usuario no especifica nada, igual que un hispanohablante
entendería "tomate" como el tomate normal, no el de árbol, salvo que lo
aclare explícitamente.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import get_settings
from app.services.text_utils import (
    limpiar_nombre_producto,
    normalize_basic,
    palabras_de_contenido,
    similitud_maxima_palabras,
)

settings = get_settings()

_UMBRAL_DISTINTIVO = 0.6  # mismo umbral calibrado que normalizer.py


def _redis_key(phone: str) -> str:
    return f"mercoia:ambiguo:{phone}"


@dataclass
class OpcionAmbigua:
    nombre: str  # nombre tal cual en el boletín, ej. "Papa negra*"
    distintivo: str  # palabra(s) normalizadas que la diferencian del resto del grupo


@dataclass
class GrupoAmbiguo:
    base: str  # palabra base compartida, normalizada, ej. "papa"
    opciones: list[OpcionAmbigua]


def agrupar_por_base(productos: list[dict]) -> list[GrupoAmbiguo]:
    """Agrupa `productos` (lista de {"x", "nombre"} de sipsa_parser) por la
    primera palabra de su nombre normalizado. Solo devuelve grupos con 2+
    variedades reales ese día, y descarta los grupos donde una variedad es
    el nombre "pelado" (esa siempre gana por defecto, no hace falta
    preguntar)."""
    por_base: dict[str, list[str]] = {}

    for p in productos:
        nombre = p.get("nombre")
        if not nombre:
            continue
        palabras = normalize_basic(nombre).split(" ")
        palabras = [w for w in palabras if w]
        if not palabras:
            continue
        base, resto = palabras[0], palabras[1:]
        por_base.setdefault(base, []).append(nombre)
        _ = resto  # el distintivo se recalcula abajo con limpiar_nombre_producto

    grupos: list[GrupoAmbiguo] = []
    for base, nombres in por_base.items():
        if len(nombres) < 2:
            continue

        opciones = []
        hay_nombre_pelado = False
        for nombre in nombres:
            palabras = normalize_basic(nombre).split(" ")
            palabras = [w for w in palabras if w]
            resto = palabras[1:]
            distintivo = " ".join(resto)
            if not distintivo:
                hay_nombre_pelado = True
                break
            opciones.append(OpcionAmbigua(nombre=nombre, distintivo=distintivo))

        if hay_nombre_pelado:
            continue  # el nombre pelado es el default implícito, no hay nada que aclarar
        if len(opciones) < 2:
            continue

        grupos.append(GrupoAmbiguo(base=base, opciones=opciones))

    return grupos


def detectar_ambiguedad(mensaje_usuario: str, productos: list[dict]) -> GrupoAmbiguo | None:
    """Si `mensaje_usuario` menciona la palabra base de un grupo con varias
    variedades, SIN mencionar ya el distintivo de ninguna opción concreta,
    devuelve ese grupo. `None` si el mensaje no es ambiguo."""
    grupos = agrupar_por_base(productos)
    if not grupos:
        return None

    palabras_msg = set(palabras_de_contenido(mensaje_usuario))

    for grupo in grupos:
        if grupo.base not in palabras_msg:
            continue

        ya_especifico = any(
            similitud_maxima_palabras(mensaje_usuario, opcion.distintivo) >= _UMBRAL_DISTINTIVO
            for opcion in grupo.opciones
        )
        if not ya_especifico:
            return grupo

    return None


def construir_respuesta_ambigua(grupo: GrupoAmbiguo) -> str:
    lineas_opciones = "\n".join(f"• {limpiar_nombre_producto(o.nombre)}" for o in grupo.opciones)
    ejemplo = grupo.opciones[0].distintivo.split(" ")[0]
    return (
        f'🔎 Encontré más de una opción relacionada con "{grupo.base.capitalize()}".\n\n'
        "¿Cuál precio deseas consultar?\n\n"
        f"{lineas_opciones}\n\n"
        f'Puedes responder solo con la palabra que la distingue (por ejemplo: "{ejemplo}").'
    )


@dataclass
class ResolucionPendiente:
    resuelto: bool
    mensaje_usuario: str


async def resolver_pendiente(redis: Redis, phone: str, mensaje_usuario: str) -> ResolucionPendiente:
    """Si había un grupo ambiguo pendiente para este teléfono (de la
    respuesta anterior del bot) y el mensaje actual coincide con alguna de
    sus opciones, arma el mensaje efectivo combinando la pregunta original
    con la variedad elegida y borra el contexto. Si no coincide con nada,
    también borra el contexto (se asume que el usuario cambió de tema) y
    deja el mensaje tal cual, para tratarlo como una consulta nueva."""
    key = _redis_key(phone)
    raw = await redis.get(key)
    if not raw:
        return ResolucionPendiente(False, mensaje_usuario)

    try:
        contexto = json.loads(raw)
    except json.JSONDecodeError:
        await redis.delete(key)
        return ResolucionPendiente(False, mensaje_usuario)

    mejor_opcion = None
    mejor_similitud = 0.0
    for opcion in contexto["opciones"]:
        similitud = similitud_maxima_palabras(mensaje_usuario, opcion["distintivo"])
        if similitud > mejor_similitud:
            mejor_similitud = similitud
            mejor_opcion = opcion

    await redis.delete(key)

    if mejor_opcion and mejor_similitud >= _UMBRAL_DISTINTIVO:
        nombre_limpio = limpiar_nombre_producto(mejor_opcion["nombre"])
        mensaje_efectivo = f"{contexto['mensaje_original']} {nombre_limpio}".strip()
        return ResolucionPendiente(True, mensaje_efectivo)

    return ResolucionPendiente(False, mensaje_usuario)


async def guardar_contexto(redis: Redis, phone: str, grupo: GrupoAmbiguo, mensaje_original: str) -> None:
    payload = {
        "base": grupo.base,
        "mensaje_original": mensaje_original,
        "opciones": [{"nombre": o.nombre, "distintivo": o.distintivo} for o in grupo.opciones],
    }
    await redis.set(_redis_key(phone), json.dumps(payload), ex=settings.ambiguous_context_ttl_seconds)


async def olvidar_pendiente(redis: Redis, phone: str) -> None:
    """Descarta una desambiguación pendiente sin esperar a que el usuario la
    responda (ej. si el usuario pidió volver al menú inicial a mitad de
    camino) — evita que un mensaje posterior sin relación se interprete por
    error como la respuesta a esta pregunta vieja."""
    await redis.delete(_redis_key(phone))
