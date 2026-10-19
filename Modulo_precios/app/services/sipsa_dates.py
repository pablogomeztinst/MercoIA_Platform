"""Interpretación de fechas en lenguaje natural (español) para ubicar el
boletín SIPSA Diario correcto y generación de las URLs candidatas en
dane.gov.co.

Réplica en Python del nodo "Generar URLs SIPSA candidatas" del flujo de n8n.

Soporta las mismas expresiones que el flujo original:
    hoy, ayer, antes de ayer / anteayer / antier, hace 2 días / hace dos días,
    martes 16, del miércoles / miércoles pasado, 10/06/2026, 10 de junio...

Si el usuario NO pide fecha: se generan candidatos desde hoy hacia atrás
(saltando fines de semana) hasta `sipsa_max_dias_atras`, para ubicar la base
más reciente disponible. Si el usuario SÍ pide una fecha: se genera solo el
candidato de esa fecha exacta.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.text_utils import normalize_basic

settings = get_settings()

MESES_DANE = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

MESES_TEXTO = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
MESES_TEXTO_RE = "|".join(MESES_TEXTO.keys())

MESES_DISPLAY = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]
DIAS_SEMANA_DISPLAY = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

NUMEROS_TEXTO = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30,
}
NUMEROS_TEXTO_RE = "|".join(sorted(NUMEROS_TEXTO.keys(), key=len, reverse=True))

DIAS_SEMANA_NUMERO = {  # domingo=0 ... sabado=6, igual que Date.getUTCDay() en JS
    "domingo": 0, "lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4,
    "viernes": 5, "sabado": 6,
}
DIAS_SEMANA_RE = "|".join(DIAS_SEMANA_NUMERO.keys())


def _limpiar_fecha(texto: str) -> str:
    return normalize_basic(texto, keep_chars="/-.")


def _capitalizar(texto: str) -> str:
    return texto[:1].upper() + texto[1:] if texto else texto


def _dia_semana_js(fecha: dt.date) -> int:
    """Índice de día de semana estilo JS Date.getUTCDay(): domingo=0 ... sábado=6."""
    return (fecha.weekday() + 1) % 7


def es_fin_de_semana(fecha: dt.date) -> bool:
    return fecha.weekday() in (5, 6)  # sábado=5, domingo=6 en Python


def fecha_legible_larga(fecha: dt.date) -> str:
    dia_semana = DIAS_SEMANA_DISPLAY[fecha.weekday()]
    mes = MESES_DISPLAY[fecha.month - 1]
    return f"{_capitalizar(dia_semana)} {fecha.day} de {mes} de {fecha.year}"


def obtener_fecha_actual_colombia() -> dt.date:
    return dt.datetime.now(ZoneInfo(settings.timezone)).date()


def _encontrar_ultimo_dia_semana(
    fecha_base: dt.date, dia_objetivo: int, forzar_pasado: bool = False
) -> dt.date:
    hoy_semana = _dia_semana_js(fecha_base)
    diferencia = hoy_semana - dia_objetivo
    if diferencia < 0:
        diferencia += 7
    if forzar_pasado and diferencia == 0:
        diferencia = 7
    return fecha_base - dt.timedelta(days=diferencia)


@dataclass
class FechaSolicitada:
    tiene_fecha: bool
    tipo_fecha: str
    descripcion_fecha: str | None
    offset_dias: int | None
    fecha: dt.date | None


def _fecha_valida(anio: int, mes: int, dia: int) -> bool:
    try:
        dt.date(anio, mes, dia)
        return True
    except ValueError:
        return False


def detectar_fecha_solicitada(mensaje_usuario: str, fecha_base: dt.date) -> FechaSolicitada:
    mensaje = _limpiar_fecha(mensaje_usuario)
    anio_actual = fecha_base.year
    mes_actual = fecha_base.month

    # 1. Antes de ayer / anteayer / antier (debe ir antes que "ayer")
    if re.search(r"\bantes\s+de\s+ayer\b", mensaje) or "anteayer" in mensaje.split() \
            or "antier" in mensaje.split():
        return FechaSolicitada(True, "anteayer", "anteayer", 2, fecha_base - dt.timedelta(days=2))

    # 2. Hoy
    if re.search(r"\bhoy\b", mensaje):
        return FechaSolicitada(True, "hoy", "hoy", 0, fecha_base)

    # 3. Ayer
    if re.search(r"\bayer\b", mensaje):
        return FechaSolicitada(True, "ayer", "ayer", 1, fecha_base - dt.timedelta(days=1))

    # 4. Hace X días
    match_hace = re.search(
        rf"\bhace\s+(\d+|{NUMEROS_TEXTO_RE})\s+dias?\b", mensaje
    )
    if match_hace:
        valor = match_hace.group(1)
        dias = int(valor) if valor.isdigit() else NUMEROS_TEXTO.get(valor)
        if dias is not None and dias >= 0:
            desc = f"hace {dias} día{'' if dias == 1 else 's'}"
            return FechaSolicitada(True, "hace_dias", desc, dias, fecha_base - dt.timedelta(days=dias))

    # 5. Fecha numérica: 10/06/2026, 10-06-2026, 10.06.2026 o 10/06
    match_num = re.search(r"(?:^|\D)(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?(?:\D|$)", mensaje)
    if match_num:
        dia, mes = int(match_num.group(1)), int(match_num.group(2))
        anio = int(match_num.group(3)) if match_num.group(3) else anio_actual
        if anio < 100:
            anio += 2000
        if _fecha_valida(anio, mes, dia):
            desc = f"{dia:02d}/{mes:02d}/{anio}"
            return FechaSolicitada(True, "fecha_numerica", desc, None, dt.date(anio, mes, dia))

    # 6. Fecha textual: 10 de junio, 10 junio, 10 de junio de 2026
    match_texto = re.search(
        rf"\b(\d{{1,2}})\s+(?:de\s+)?({MESES_TEXTO_RE})(?:\s+de)?\s*(\d{{4}})?\b", mensaje
    )
    if match_texto:
        dia = int(match_texto.group(1))
        mes = MESES_TEXTO[match_texto.group(2)]
        anio = int(match_texto.group(3)) if match_texto.group(3) else anio_actual
        if _fecha_valida(anio, mes, dia):
            desc = f"{dia} de {match_texto.group(2)} de {anio}"
            return FechaSolicitada(True, "fecha_textual", desc, None, dt.date(anio, mes, dia))

    # 7. Día de semana con número: martes 16, el martes 16 [de mes] [de año]
    match_dia_num = re.search(
        rf"\b({DIAS_SEMANA_RE})\s+(\d{{1,2}})(?:\s+(?:de\s+)?({MESES_TEXTO_RE}))?(?:\s+de)?\s*(\d{{4}})?\b",
        mensaje,
    )
    if match_dia_num:
        dia_semana_texto = match_dia_num.group(1)
        dia = int(match_dia_num.group(2))
        mes = MESES_TEXTO[match_dia_num.group(3)] if match_dia_num.group(3) else mes_actual
        anio = int(match_dia_num.group(4)) if match_dia_num.group(4) else anio_actual

        if _fecha_valida(anio, mes, dia):
            fecha = dt.date(anio, mes, dia)
            # Si no se especificó mes y la fecha cae en el futuro, asumir mes anterior.
            if not match_dia_num.group(3) and fecha > fecha_base:
                mes_prev = mes - 1 or 12
                anio_prev = anio if mes > 1 else anio - 1
                if _fecha_valida(anio_prev, mes_prev, dia):
                    fecha = dt.date(anio_prev, mes_prev, dia)
            return FechaSolicitada(
                True, "dia_semana_con_numero", f"{dia_semana_texto} {dia}", None, fecha
            )

    # 8. Día de semana solamente: del miércoles, miércoles pasado, el miércoles
    match_dia = re.search(rf"\b({DIAS_SEMANA_RE})\b", mensaje)
    if match_dia:
        dia_semana_texto = match_dia.group(1)
        dia_objetivo = DIAS_SEMANA_NUMERO[dia_semana_texto]
        forzar_pasado = "pasado" in mensaje
        fecha = _encontrar_ultimo_dia_semana(fecha_base, dia_objetivo, forzar_pasado)
        desc = f"{dia_semana_texto} pasado" if forzar_pasado else dia_semana_texto
        return FechaSolicitada(True, "dia_semana", desc, None, fecha)

    return FechaSolicitada(False, "sin_fecha", None, None, None)


@dataclass
class CandidatoFecha:
    intento: int
    fecha_dane: str
    fecha_legible: str
    fecha_legible_larga: str
    fecha_objetivo_iso: str
    dia_semana: str
    url_sipsa: str
    modo_busqueda_fecha: str
    fecha_solicitada: bool
    tipo_fecha_solicitada: str | None
    descripcion_fecha_solicitada: str | None
    offset_dias_solicitado: int | None
    es_fecha_hoy: bool = False


def _crear_candidatos_para_fecha(fecha: dt.date, **opciones) -> list[dict]:
    dia_sin_cero = str(fecha.day)
    dia_con_cero = f"{fecha.day:02d}"
    mes_dane = MESES_DANE[fecha.month - 1]

    fecha_dane_sin_cero = f"{dia_sin_cero}{mes_dane}{fecha.year}"
    fecha_dane_con_cero = f"{dia_con_cero}{mes_dane}{fecha.year}"

    fecha_legible = f"{dia_con_cero}/{fecha.month:02d}/{fecha.year}"
    fecha_legible_larga_ = fecha_legible_larga(fecha)
    fecha_iso = fecha.isoformat()
    dia_semana = DIAS_SEMANA_DISPLAY[fecha.weekday()]

    base = dict(
        fecha_legible=fecha_legible,
        fecha_legible_larga=fecha_legible_larga_,
        fecha_objetivo_iso=fecha_iso,
        dia_semana=dia_semana,
        **opciones,
    )

    candidatos = [
        {
            **base,
            "fecha_dane": fecha_dane_sin_cero,
            "url_sipsa": settings.sipsa_base_url.format(fecha=fecha_dane_sin_cero),
        }
    ]
    if fecha_dane_con_cero != fecha_dane_sin_cero:
        candidatos.append(
            {
                **base,
                "fecha_dane": fecha_dane_con_cero,
                "url_sipsa": settings.sipsa_base_url.format(fecha=fecha_dane_con_cero),
            }
        )
    return candidatos


def generar_candidatos(mensaje_usuario: str) -> list[CandidatoFecha]:
    fecha_base = obtener_fecha_actual_colombia()
    solicitud = detectar_fecha_solicitada(mensaje_usuario, fecha_base)

    crudos: list[dict] = []

    if solicitud.tiene_fecha:
        crudos = _crear_candidatos_para_fecha(
            solicitud.fecha,
            modo_busqueda_fecha="fecha_especifica",
            fecha_solicitada=True,
            tipo_fecha_solicitada=solicitud.tipo_fecha,
            descripcion_fecha_solicitada=solicitud.descripcion_fecha,
            offset_dias_solicitado=solicitud.offset_dias,
        )
    else:
        for offset in range(0, settings.sipsa_max_dias_atras + 1):
            candidata = fecha_base - dt.timedelta(days=offset)
            if es_fin_de_semana(candidata):
                continue
            crudos.extend(
                _crear_candidatos_para_fecha(
                    candidata,
                    modo_busqueda_fecha="mas_reciente",
                    fecha_solicitada=False,
                    tipo_fecha_solicitada=None,
                    descripcion_fecha_solicitada=None,
                    offset_dias_solicitado=offset,
                    es_fecha_hoy=(offset == 0),
                )
            )

    return [CandidatoFecha(intento=i + 1, **c) for i, c in enumerate(crudos)]
