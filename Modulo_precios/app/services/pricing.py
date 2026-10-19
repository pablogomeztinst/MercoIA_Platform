"""Construcción del mensaje final de precios.

Réplica del nodo "Code: obtener precios reales" del flujo de n8n:
- precio actual en negrilla (formato WhatsApp) por cada mercado,
- variación en pesos calculada a partir del Var % que trae SIPSA
  (corrigiendo el caso en que Excel entrega 4% como 0.04),
- sin mostrar porcentajes ni signos al usuario, solo pesos y una flecha,
- artículo correcto según el género del producto (del banano, de la papa...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.sipsa_dates import CandidatoFecha, obtener_fecha_actual_colombia
from app.services.sipsa_parser import FilaSipsa
from app.services.text_utils import limpiar_nombre_producto, normalize_basic

MESES_TEXTO = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

FEMENINOS = {
    "ahuyama", "arveja", "arracacha", "berenjena", "cebolla", "coliflor",
    "curuba", "espinaca", "fresa", "granadilla", "guanabana", "guayaba",
    "habichuela", "lechuga", "mandarina", "mora", "naranja", "papa",
    "papaya", "patilla", "pera", "pina", "remolacha", "uchuva", "uva",
    "yuca", "zanahoria",
}
MASCULINOS = {
    "aguacate", "ajo", "apio", "banano", "brocoli", "cilantro", "coco",
    "frijol", "limon", "lulo", "maiz", "mango", "maracuya", "melon",
    "pepino", "pimenton", "platano", "repollo", "tomate",
}

_NO_DISPONIBLE = {"n.d.", "n.d", "-"}


def _precio_a_numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return round(float(valor))
    texto = str(valor).strip().lower()
    if texto in _NO_DISPONIBLE or "no disponible" in texto:
        return None
    limpio = texto.replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return round(float(limpio))
    except ValueError:
        return None


def _porcentaje_a_numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        numero = float(valor)
        if abs(numero) <= 1:
            numero *= 100
        return numero
    texto_original = str(valor).strip()
    contiene_pct = "%" in texto_original
    texto = texto_original.lower().replace("%", "").replace(" ", "").replace(",", ".")
    if texto in _NO_DISPONIBLE or "no disponible" in texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    if not contiene_pct and 0 < abs(numero) <= 1:
        numero *= 100
    return numero


def _formatear_numero_cop(numero: float | None) -> str:
    if numero is None:
        return "n.d."
    return f"{round(numero):,}".replace(",", ".")


def _formatear_precio_cop(valor) -> str:
    numero = _precio_a_numero(valor)
    return "n.d." if numero is None else _formatear_numero_cop(numero)


def precio_disponible(valor) -> bool:
    """True si `valor` es un precio real (no None, no "n.d.", no vacío).
    Público porque conversation.py lo usa para decidir si debe buscar un
    precio de respaldo en boletines anteriores para ese mercado puntual."""
    return _precio_a_numero(valor) is not None


def formatear_precio_cop(valor) -> str:
    """Wrapper público de `_formatear_precio_cop` (formato $ es-CO, sin
    decimales), para reusar el mismo formato de precio fuera de este módulo
    -- ver resumen.py."""
    return _formatear_precio_cop(valor)


def _calcular_variacion_pesos(precio_raw, var_raw) -> float | None:
    precio = _precio_a_numero(precio_raw)
    var_pct = _porcentaje_a_numero(var_raw)
    if precio is None or var_pct is None:
        return None
    factor = 1 + var_pct / 100
    if factor == 0:
        return None
    precio_anterior = precio / factor
    return round(precio - precio_anterior)


def _texto_variacion(precio_raw, var_raw) -> str:
    variacion = _calcular_variacion_pesos(precio_raw, var_raw)
    if variacion is None:
        return "ℹ️ Variación: no disponible con respecto al día de mercado anterior."
    abs_txt = _formatear_numero_cop(abs(variacion))
    if variacion > 0:
        return f"📈 Variación: subió *{abs_txt} COP por kilogramo* con respecto al día de mercado anterior."
    if variacion < 0:
        return f"📉 Variación: disminuyó *{abs_txt} COP por kilogramo* con respecto al día de mercado anterior."
    return "➡️ Variación: se mantuvo igual con respecto al día de mercado anterior."


def _nombre_con_articulo(nombre_producto: str) -> str:
    nombre_original = limpiar_nombre_producto(nombre_producto)
    nombre_normalizado = normalize_basic(nombre_original)
    if not nombre_normalizado:
        return "del producto"

    primera_palabra = nombre_normalizado.split(" ")[0]
    frase = nombre_original.lower()

    if primera_palabra in FEMENINOS:
        return f"de la {frase}"
    if primera_palabra in MASCULINOS:
        return f"del {frase}"
    if primera_palabra.endswith("a"):
        return f"de la {frase}"
    if primera_palabra[-1:] in ("o", "e", "n", "l", "r"):
        return f"del {frase}"
    return f"de {frase}"


def _frase_mercado(
    region: str, mercado: str, nombre_producto: str, precio_raw, var_raw, nota_fecha: str | None = None
) -> str:
    nombre_con_articulo = _nombre_con_articulo(nombre_producto)
    precio_fmt = _formatear_precio_cop(precio_raw)
    variacion = _texto_variacion(precio_raw, var_raw)

    if precio_fmt == "n.d.":
        return (
            f"📍 En {region}, según {mercado}, no hay información disponible sobre "
            f"el precio {nombre_con_articulo}.\n{variacion}"
        )

    nota = f"\n_(dato más reciente disponible de este mercado: {nota_fecha})_" if nota_fecha else ""
    return (
        f"📍 En {region}, según {mercado}, el precio {nombre_con_articulo} es de "
        f"*{precio_fmt} COP por kilogramo*.{nota}\n{variacion}"
    )


def _parsear_fecha_reporte(texto_fecha: str) -> tuple[int, int, int] | None:
    texto = normalize_basic(texto_fecha)
    match = re.search(r"(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})", texto)
    if not match:
        return None
    dia, mes_texto, anio = int(match.group(1)), match.group(2), int(match.group(3))
    mes = MESES_TEXTO.get(mes_texto)
    if not mes:
        return None
    return anio, mes, dia


@dataclass
class ResultadoPrecio:
    encontrado: bool
    respuesta_base: str
    nombre_escogido: str | None = None
    precio_centroabastos: str | None = None
    precio_corabastos: str | None = None
    fecha_reporte: str | None = None


def build_pricing_response(
    normalizado: dict | None,
    filas: list[FilaSipsa],
    candidato: CandidatoFecha,
    *,
    respaldo_centroabastos: tuple[object, object, str] | None = None,
    respaldo_corabastos: tuple[object, object, str] | None = None,
) -> ResultadoPrecio:
    """`respaldo_centroabastos` / `respaldo_corabastos`, si se pasan, son
    tuplas `(precio, var_pct, fecha_legible_larga)` encontradas por
    conversation.py buscando hacia atrás cuando el boletín del día no
    reportó ese mercado puntual (ver sipsa_fetch.buscar_precio_respaldo).
    Solo se usan si el boletín de referencia no trae un precio válido para
    ese mercado; la fecha general del reporte (`fecha_reporte_texto`) nunca
    cambia por esto, solo se agrega una nota junto al precio de respaldo."""
    if not normalizado:
        return ResultadoPrecio(
            False,
            "No pude interpretar el producto solicitado. Por favor escribe solamente "
            "el nombre del producto que deseas consultar. 🙂",
        )

    try:
        x = int(normalizado.get("x", -1))
    except (TypeError, ValueError):
        x = -1

    if not normalizado.get("encontrado") or x == -1 or x < 0 or x >= len(filas):
        return ResultadoPrecio(
            False,
            "No encontré ese producto exacto en la base disponible. Por favor escribe "
            "nuevamente el nombre del producto que deseas consultar. 🙂",
        )

    fila = filas[x]
    fecha_reporte_texto = filas[0].nombre or "último reporte disponible"
    fecha_reporte_partes = _parsear_fecha_reporte(fecha_reporte_texto)
    fecha_reporte_iso = (
        f"{fecha_reporte_partes[0]:04d}-{fecha_reporte_partes[1]:02d}-{fecha_reporte_partes[2]:02d}"
        if fecha_reporte_partes
        else None
    )

    hoy = obtener_fecha_actual_colombia()
    reporte_es_de_hoy = bool(
        fecha_reporte_partes
        and (fecha_reporte_partes[2], fecha_reporte_partes[1], fecha_reporte_partes[0])
        == (hoy.day, hoy.month, hoy.year)
    )

    nombre_original = fila.nombre or normalizado.get("nombre_escogido") or "Producto"
    nombre = limpiar_nombre_producto(nombre_original)
    nombre_con_articulo = _nombre_con_articulo(nombre)

    # ---------- Mensaje de disponibilidad ----------
    if candidato.fecha_solicitada and candidato.modo_busqueda_fecha == "fecha_especifica":
        if fecha_reporte_iso and fecha_reporte_iso == candidato.fecha_objetivo_iso:
            disponibilidad = f"📅 Información consultada para la fecha solicitada: {fecha_reporte_texto}."
        else:
            fecha_pedida = candidato.fecha_legible_larga or candidato.descripcion_fecha_solicitada or "la fecha indicada"
            disponibilidad = (
                f"⚠️ Se solicitó información para {fecha_pedida}.\n\n"
                f"La base encontrada corresponde a: {fecha_reporte_texto}."
            )
    else:
        if fecha_reporte_partes:
            if reporte_es_de_hoy:
                disponibilidad = f"✅ Información disponible para hoy: {fecha_reporte_texto}."
            else:
                disponibilidad = (
                    "⚠️ No encontré información disponible para el día de hoy.\n\n"
                    f"La información más reciente disponible corresponde a: {fecha_reporte_texto}."
                )
        else:
            disponibilidad = f"ℹ️ Información disponible: {fecha_reporte_texto}."

    precio_centro, var_centro, nota_centro = fila.precio_centroabastos, fila.var_centroabastos, None
    if not precio_disponible(precio_centro) and respaldo_centroabastos:
        precio_centro, var_centro, nota_centro = respaldo_centroabastos
    elif precio_disponible(precio_centro) and fila.nota_fecha:
        # La fila completa vino de otra fecha/fuente (ver
        # sipsa_fetch._completar_con_fuente_principal) -- se aclara para no
        # dar a entender que este precio es del día del informe.
        nota_centro = fila.nota_fecha

    precio_cora, var_cora, nota_cora = fila.precio_corabastos, fila.var_corabastos, None
    if not precio_disponible(precio_cora) and respaldo_corabastos:
        precio_cora, var_cora, nota_cora = respaldo_corabastos
    elif precio_disponible(precio_cora) and fila.nota_fecha:
        nota_cora = fila.nota_fecha

    respuesta_base = (
        f"📊 Informe de precios {nombre_con_articulo}:\n\n"
        f"{_frase_mercado('Santander', 'Centroabastos S.A.', nombre, precio_centro, var_centro, nota_centro)}\n\n"
        f"{_frase_mercado('Bogotá D.C.', 'Corabastos S.A.', nombre, precio_cora, var_cora, nota_cora)}\n\n"
        f"{disponibilidad}\n\n"
        "🙂 ¿Deseas consultar otro precio?"
    ).strip()

    return ResultadoPrecio(
        True,
        respuesta_base,
        nombre_escogido=nombre,
        precio_centroabastos=_formatear_precio_cop(precio_centro),
        precio_corabastos=_formatear_precio_cop(precio_cora),
        fecha_reporte=fecha_reporte_texto,
    )
