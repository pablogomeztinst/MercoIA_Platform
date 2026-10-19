"""Resumen de precios de los N alimentos principales para Colombia, en un
solo mensaje ligero (activado con la palabra "resumen", ver classifier.py).

Por cada producto se muestra un solo precio: el del mercado (Bogotá D.C.-
Corabastos o Bucaramanga-Centroabastos) que tenga la información MÁS
RECIENTE, no uno fijo por defecto -- si el boletín del día no reportó un
mercado puntual para ese producto, se busca hacia atrás (igual que ya hace
conversation.py para una consulta individual, ver `buscar_precio_respaldo`
en sipsa_fetch.py) y se compara la fecha real de cada mercado antes de
decidir cuál mostrar. Esta búsqueda hacia atrás solo aplica cuando se pidió
"el más reciente" (`modo_busqueda_fecha == "mas_reciente"`); si el usuario
pidió una fecha puntual (ej. "resumen de ayer"), se muestra tal cual lo que
haya ese día exacto, sin completar con otros días.
"""
from __future__ import annotations

import httpx
from dataclasses import dataclass
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import sipsa_fetch
from app.services.pricing import formatear_precio_cop, precio_disponible
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_parser import FilaSipsa
from app.services.text_utils import limpiar_nombre_producto, normalize_basic

# N=10 alimentos más representativos para Colombia, de los que sí cubre el
# catálogo actual (Frutas, Tubérculos/raíces/plátanos, Verduras y
# hortalizas). Cuando un producto tiene varias variedades en SIPSA, se elige
# la más común/representativa para que el resumen quede en una sola línea
# por alimento.
PRODUCTOS_RESUMEN = [
    ("🥔", "Papa parda pastusa"),
    ("🍌", "Plátano hartón verde"),
    ("🌾", "Yuca criolla"),
    ("🧅", "Cebolla cabezona blanca"),
    ("🧅", "Cebolla junca"),
    ("🍅", "Tomate chonto"),
    ("🥕", "Zanahoria"),
    ("🥑", "Aguacate Hass"),
    ("🍋", "Limón Tahití"),
    ("🍌", "Banano criollo"),
]


@dataclass
class _ValorMercado:
    mercado: str  # "Bogotá" | "Bucaramanga"
    precio: object
    fecha_objetivo_iso: str
    fecha_legible_larga: str


async def _valor_mercado(
    nombre_normalizado: str,
    fila: FilaSipsa | None,
    campo_precio: str,
    campo_var: str,
    mercado: str,
    candidato_usado: CandidatoFecha,
    candidatos_restantes: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> _ValorMercado | None:
    """Precio de un mercado puntual para este producto, con su fecha real --
    la del boletín actual si ese mercado sí reportó ese día, o la del
    boletín más reciente anterior donde sí aparezca (búsqueda hacia atrás,
    igual que para una consulta individual)."""
    if fila and precio_disponible(getattr(fila, campo_precio)):
        return _ValorMercado(mercado, getattr(fila, campo_precio), candidato_usado.fecha_objetivo_iso, candidato_usado.fecha_legible_larga)

    if not candidatos_restantes:
        return None

    encontrado = await sipsa_fetch.buscar_precio_respaldo(
        nombre_normalizado, campo_precio, campo_var, candidatos_restantes, redis=redis, db=db, http_client=http_client
    )
    if not encontrado:
        return None

    precio, _var, candidato_fuente = encontrado
    return _ValorMercado(mercado, precio, candidato_fuente.fecha_objetivo_iso, candidato_fuente.fecha_legible_larga)


async def _resolver_producto(
    nombre: str,
    filas: list[FilaSipsa],
    candidato_usado: CandidatoFecha,
    candidatos: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> tuple[object, str | None, str | None]:
    """(precio, mercado, nota_fecha) para `nombre`: el mercado con la
    información más reciente disponible. `mercado`/`precio` son `None` si no
    se encontró nada en ninguno; `nota_fecha` solo se llena si el precio
    elegido es de un día distinto al del boletín principal (para que
    pricing.py-style se lo aclare al usuario, igual que en una consulta
    individual)."""
    nombre_normalizado = normalize_basic(nombre)
    fila = next((f for f in filas if normalize_basic(f.nombre) == nombre_normalizado), None)

    permite_respaldo = candidato_usado.modo_busqueda_fecha == "mas_reciente"
    candidatos_restantes: list[CandidatoFecha] = []
    if permite_respaldo:
        offset_usado = candidato_usado.offset_dias_solicitado or 0
        candidatos_restantes = [
            c for c in candidatos if c.offset_dias_solicitado is not None and c.offset_dias_solicitado > offset_usado
        ]

    cora = await _valor_mercado(
        nombre_normalizado, fila, "precio_corabastos", "var_corabastos", "Bogotá",
        candidato_usado, candidatos_restantes, redis=redis, db=db, http_client=http_client,
    )
    centro = await _valor_mercado(
        nombre_normalizado, fila, "precio_centroabastos", "var_centroabastos", "Bucaramanga",
        candidato_usado, candidatos_restantes, redis=redis, db=db, http_client=http_client,
    )

    opciones = [v for v in (cora, centro) if v is not None]
    if not opciones:
        return None, None, None

    # El más reciente por fecha ISO; en empate se queda con el primero de la
    # lista (Bogotá), igual que antes cuando ambos son del mismo día.
    elegido = max(opciones, key=lambda v: v.fecha_objetivo_iso)
    nota_fecha = elegido.fecha_legible_larga if elegido.fecha_objetivo_iso != candidato_usado.fecha_objetivo_iso else None
    return elegido.precio, elegido.mercado, nota_fecha


async def construir_resumen(
    filas: list[FilaSipsa],
    candidato_usado: CandidatoFecha,
    candidatos: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> str:
    lineas = []
    for emoji, nombre in PRODUCTOS_RESUMEN:
        nombre_limpio = limpiar_nombre_producto(nombre)
        precio, mercado, nota_fecha = await _resolver_producto(
            nombre, filas, candidato_usado, candidatos, redis=redis, db=db, http_client=http_client
        )

        if mercado is None:
            lineas.append(f"{emoji} {nombre_limpio}: _no disponible_")
            continue

        nota = f" _({mercado}"
        nota += f", {nota_fecha})_" if nota_fecha else ")_"
        lineas.append(f"{emoji} {nombre_limpio}: *{formatear_precio_cop(precio)} COP/kg*{nota}")

    return (
        "📊 *Resumen de precios mayoristas*\n"
        "_(se muestra, por producto, el mercado -Bogotá o Bucaramanga- con la "
        "información más reciente disponible)_\n\n"
        + "\n".join(lineas)
        + "\n\n✍️ Escribe el nombre de cualquiera de estos productos (u otro) "
        "para ver el detalle completo con los dos mercados y la variación.\n\n"
        "🙂 ¿Deseas consultar otro precio?"
    )
