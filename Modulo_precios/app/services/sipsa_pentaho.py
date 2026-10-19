"""Fuente de datos SIPSA vía el dashboard Pentaho del DANE (API CDA -
Community Data Access), que reemplaza al Excel estático "SIPSA Diario" como
fuente principal. El Excel viejo (ver sipsa_parser.py) queda como respaldo
automático si esta fuente falla para un día puntual (ver sipsa_fetch.py).

URL del dashboard visual (lo que un humano usaría a mano, con los filtros
Fuente = Bogotá Corabastos + Bucaramanga Centroabastos, Grupo = Frutas +
Tubérculos/raíces/plátanos + Verduras y hortalizas, Producto = Marcar Todos,
Periodicidad = Diario, botón Exportar > Tabla):
https://apps.dane.gov.co/pentaho/api/repos/%3Apublic%3ASIPSA%3ASIPSAV17.wcdf/generatedContent

Esa URL solo carga el dashboard (JavaScript); la petición real que produce
la tabla de datos la dispara el botón "Exportar" contra la API CDA:
    POST /pentaho/plugin/cda/api/doQuery
        path=/public/SIPSA/SIPSAV17.cda
        dataAccessId=qryTabla (o qryArticulo, qryGrupo, qryFuente...)

Esto se reconstruyó capturando esa petición real con las DevTools del
navegador (ninguna de las convenciones "estándar" de CDA para parámetros de
tipo lista -JSON, corchetes, valores repetidos- funcionaba sin más: el detalle
que faltaba es que el dashboard antepone el prefijo literal "param" al
nombre de cada parámetro declarado en el .cda, ej. "paramparcFuente" en vez
de "parcFuente". Sin ese prefijo, CDA responde 200 con 0 filas, sin error.

A diferencia del Excel viejo, esta fuente NO trae variación (%) -- devuelve
solo fecha/fuente/artículo/promedio/mínimo/máximo. La variación se calcula
aquí mismo comparando el precio del día pedido contra el precio disponible
más reciente dentro de `sipsa_pentaho_lookback_dias`, como fracción (ej.
-0.032 para -3.2%) -- la misma convención numérica que ya esperaba
pricing.py del Excel viejo (ver `_porcentaje_a_numero`), así que no hizo
falta tocar esa parte del pipeline.

"Cortacircuitos" ante caídas de Pentaho (agregado tras un incidente real en
producción, 17-sep-2026): esta función se puede invocar MUCHAS veces por un
solo mensaje de usuario (una vez por día candidato al buscar la base más
reciente, y hasta 20 veces más si el mensaje activa el resumen -- 10
productos x 2 mercados, ver resumen.py -- cada uno buscando hacia atrás por
su cuenta). Sin protección, una caída de Pentaho se traduce en decenas de
intentos de red fallidos (hasta 30s cada uno) para UN SOLO mensaje, lo que
puede tardar tanto en responder que WhatsApp reintrega el mismo mensaje o el
usuario reenvía por su cuenta -- eso fue exactamente lo que causó respuestas
duplicadas/inconsistentes para un usuario real. Por eso, apenas una consulta
a Pentaho falla (red o 5xx), se marca "caído" en Redis por
`sipsa_pentaho_circuito_ttl_segundos`: mientras esa marca esté activa, esta
fuente se salta por completo (sin ninguna llamada de red) y sipsa_fetch.py
cae directo al Excel -- ver `_circuito_abierto`/`_abrir_circuito`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

import httpx
from redis.asyncio import Redis

from app.config import get_settings
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_parser import BoletinSipsa, FilaSipsa

logger = logging.getLogger(__name__)
settings = get_settings()

_REDIS_KEY_ARTICULOS = "mercoia:sipsa:pentaho:articulos"
_REDIS_KEY_CIRCUITO = "mercoia:sipsa:pentaho:circuito_abierto"


async def _circuito_abierto(redis: Redis) -> bool:
    """True si Pentaho falló recientemente y se debe evitar intentarlo de
    nuevo por ahora (ver docstring del módulo)."""
    return bool(await redis.get(_REDIS_KEY_CIRCUITO))


async def _abrir_circuito(redis: Redis) -> None:
    await redis.set(_REDIS_KEY_CIRCUITO, "1", ex=settings.sipsa_pentaho_circuito_ttl_segundos)


async def _un_intento(
    http_client: httpx.AsyncClient, data_access_id: str, data: dict
) -> tuple[dict | None, bool]:
    """Un solo intento contra la API CDA. Devuelve (resultado, reintentable):
    `reintentable=True` marca fallas que suelen ser un tropiezo momentáneo
    (red, 5xx) y vale la pena reintentar una vez antes de rendirse; `False`
    son fallas "duras" (formato inesperado) que no mejorarían con un
    reintento inmediato."""
    try:
        resp = await http_client.post(settings.sipsa_pentaho_url, data=data, timeout=settings.sipsa_pentaho_timeout_segundos)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        logger.warning("Fallo de red consultando Pentaho SIPSA (%s): %s", data_access_id, exc)
        return None, True

    if resp.status_code != 200:
        logger.warning("Pentaho SIPSA respondió %s para %s", resp.status_code, data_access_id)
        return None, resp.status_code >= 500

    try:
        return resp.json(), False
    except ValueError:
        logger.warning("Respuesta no-JSON de Pentaho SIPSA para %s", data_access_id)
        return None, False


async def _doquery(
    http_client: httpx.AsyncClient, data_access_id: str, params: dict[str, str | list[str]], *, redis: Redis
) -> dict | None:
    """POST genérico contra la API CDA. `params` ya debe traer cada nombre
    con el prefijo "param" incluido (ver docstring del módulo); un valor
    lista se codifica como el mismo campo repetido varias veces (así es como
    CDA espera los parámetros de tipo StringArray, ej. paramparcArticulo).

    Un tropiezo de red/5xx se reintenta UNA vez (con una pausa corta) antes
    de rendirse -- Pentaho se ha visto fallar de forma intermitente (un 503
    suelto entre respuestas normales), y sin este reintento, ese único
    tropiezo abría el cortacircuitos y bloqueaba de paso un intento
    legítimo y distinto que necesitaba pasar justo después dentro del mismo
    mensaje (ej. completar productos faltantes desde Pentaho, ver
    sipsa_fetch._completar_con_fuente_principal) -- incidente real en
    producción, 17-sep-2026: por esto "Fresa" dejó de encontrarse de nuevo
    después de agregar el cortacircuitos. Solo si el reintento TAMBIÉN falla
    se abre el cortacircuitos (ahí sí es una señal más fuerte de caída real,
    no un tropiezo aislado)."""
    data = {"path": settings.sipsa_pentaho_path, "dataAccessId": data_access_id, "outputType": "json", **params}

    resultado, reintentable = await _un_intento(http_client, data_access_id, data)
    if resultado is not None:
        return resultado

    if reintentable:
        await asyncio.sleep(settings.sipsa_pentaho_reintento_espera_segundos)
        resultado, _ = await _un_intento(http_client, data_access_id, data)
        if resultado is not None:
            return resultado

    await _abrir_circuito(redis)
    return None


async def _listar_articulos(http_client: httpx.AsyncClient, redis: Redis) -> list[str] | None:
    """Todos los productos de los grupos configurados (equivale a marcar
    "Todos" en el filtro Producto del dashboard). Se cachea con TTL largo
    porque el catálogo de productos casi no cambia día a día."""
    cacheado = await redis.get(_REDIS_KEY_ARTICULOS)
    if cacheado:
        try:
            return json.loads(cacheado)
        except json.JSONDecodeError:
            pass

    params = {"paramparcGrupo": settings.sipsa_pentaho_grupos}
    data = await _doquery(http_client, "qryArticulo", params, redis=redis)
    if not data or not data.get("resultset"):
        return None

    articulos = sorted({fila[0] for fila in data["resultset"] if fila and fila[0]})
    await redis.set(_REDIS_KEY_ARTICULOS, json.dumps(articulos), ex=settings.sipsa_pentaho_articulos_ttl_seconds)
    return articulos


async def _consultar_tabla(
    http_client: httpx.AsyncClient, articulos: list[str], fecha_ini_iso: str, fecha_fin_iso: str, *, redis: Redis
) -> list[list] | None:
    params = {
        "parampardPeriodoIni": fecha_ini_iso,
        "parampardPeriodoFin": fecha_fin_iso,
        "paramparsPrecio": "Diario",
        "paramparcFuente": settings.sipsa_pentaho_fuentes,
        "paramparcArticulo": articulos,
    }
    data = await _doquery(http_client, "qryTabla", params, redis=redis)
    if data is None:
        return None
    return data.get("resultset", [])


def _calcular_variacion(
    precios_por_fecha: dict[str, float], fecha_objetivo: str
) -> tuple[float | None, float | None]:
    """(precio_del_día, variación_fracción) para `fecha_objetivo`, comparando
    contra el precio disponible más reciente ANTES de esa fecha dentro de
    `precios_por_fecha`. `None` en variación si no hay un día anterior con
    precio dentro de la ventana consultada (ej. producto nuevo, o no se
    encontró historial suficiente)."""
    precio_hoy = precios_por_fecha.get(fecha_objetivo)
    if precio_hoy is None:
        return None, None

    fechas_anteriores = sorted(f for f in precios_por_fecha if f < fecha_objetivo)
    if not fechas_anteriores:
        return precio_hoy, None

    precio_anterior = precios_por_fecha[fechas_anteriores[-1]]
    if not precio_anterior:
        return precio_hoy, None

    return precio_hoy, (precio_hoy - precio_anterior) / precio_anterior


async def obtener_boletin_pentaho(
    candidato: CandidatoFecha, *, redis: Redis, http_client: httpx.AsyncClient
) -> BoletinSipsa | None:
    """Boletín para `candidato.fecha_objetivo_iso` vía el dashboard Pentaho
    del DANE. `None` si no hay nada publicado todavía para esa fecha (ej. fin
    de semana, festivo, o el reporte de hoy aún no sale) o si hubo un error
    de red/formato -- en ambos casos, sipsa_fetch.py sigue con el siguiente
    candidato o cae al Excel viejo como respaldo. Si Pentaho falló hace poco
    (cortacircuitos abierto, ver docstring del módulo), ni siquiera se
    intenta -- se devuelve `None` de inmediato para no repetir llamadas
    lentas y fallidas en cada mensaje mientras la fuente está caída."""
    if await _circuito_abierto(redis):
        return None

    articulos = await _listar_articulos(http_client, redis)
    if not articulos:
        return None

    fecha_fin = candidato.fecha_objetivo_iso
    fecha_ini = (
        dt.date.fromisoformat(fecha_fin) - dt.timedelta(days=settings.sipsa_pentaho_lookback_dias)
    ).isoformat()

    filas_crudas = await _consultar_tabla(http_client, articulos, fecha_ini, fecha_fin, redis=redis)
    if filas_crudas is None:
        return None

    fuente_corabastos, fuente_centroabastos = settings.sipsa_pentaho_fuentes

    # {(fuente, articulo): {fecha_iso: promedio}}
    series: dict[tuple[str, str], dict[str, float]] = {}
    for fila in filas_crudas:
        fecha, fuente, articulo, promedio = fila[0], fila[1], fila[2], fila[3]
        if promedio is None:
            continue
        series.setdefault((fuente, articulo), {})[fecha] = float(promedio)

    if not any(fecha_fin in serie for serie in series.values()):
        # No hay nada publicado todavía para esta fecha puntual -- se deja
        # que sipsa_fetch.py siga probando el candidato/día siguiente.
        return None

    filas: list[FilaSipsa] = [FilaSipsa(index=0, nombre=candidato.fecha_legible_larga)]
    for i, articulo in enumerate(articulos, start=1):
        precio_cora, var_cora = _calcular_variacion(series.get((fuente_corabastos, articulo), {}), fecha_fin)
        precio_centro, var_centro = _calcular_variacion(series.get((fuente_centroabastos, articulo), {}), fecha_fin)
        filas.append(
            FilaSipsa(
                index=i,
                nombre=articulo,
                precio_corabastos=precio_cora,
                var_corabastos=var_cora,
                precio_centroabastos=precio_centro,
                var_centroabastos=var_centro,
            )
        )

    return BoletinSipsa(fecha_reporte_texto=candidato.fecha_legible_larga, filas=filas)
