"""Descarga y caché de boletines SIPSA.

Réplica del par de nodos "Loop Over Items" + "HTTP Request" + "Descargar
XLSX1" + "Leer XLSX1" del flujo de n8n, con las mejoras pedidas por la
recomendación del PDF adjunto ("no consultaría el DANE en cada consulta"):

1. Caché en Redis por fecha resuelta (TTL configurable, por defecto ~20h):
   si dos agricultores preguntan por el mismo día, sólo se descarga una vez.
2. Persistencia en PostgreSQL de cada boletín ya parseado: si el DANE está
   caído, seguimos pudiendo responder con la última base ya descubierta,
   y evitamos volver a parsear el mismo Excel tras un reinicio del proceso.
3. Dos fuentes en vivo posibles para el mismo boletín: el dashboard Pentaho
   del DANE (ver sipsa_pentaho.py, fuente principal por defecto) y el Excel
   estático de siempre (ver sipsa_parser.py). `settings.sipsa_data_source`
   decide cuál se intenta primero; la otra se usa automáticamente como
   respaldo si la principal no responde nada para ese día puntual.
4. Ese respaldo es "provisional" cuando la fecha es muy reciente: Pentaho
   publica con 1-2 días de rezago respecto al Excel, así que si hoy se
   resuelve un día reciente vía Excel (porque Pentaho aún no lo tiene), NO
   se guarda como definitivo en Postgres -solo en Redis con TTL corto-, para
   que en unas horas se reintente Pentaho y no quede la respuesta más pobre
   del respaldo congelada para siempre (Pentaho cubre más productos que el
   Excel). Fechas ya viejas sí se guardan como definitivas, como antes.
5. Precalentamiento periódico (`precalentar`, llamado desde main.py cada
   `sipsa_precalentar_intervalo_segundos`): resuelve y cachea el boletín más
   reciente en segundo plano, para que ningún usuario real sea el primero en
   pagar el costo de una caché fría (y, de paso, si Pentaho estaba caído en
   un ciclo, el siguiente ciclo lo reintenta solo, sin que nadie tenga que
   preguntar primero). Ver el incidente del 17-sep-2026 en la conversación
   con el usuario que motivó esto.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import logging

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from app.config import get_settings
from app.models import SipsaSnapshot
from app.services import sipsa_pentaho
from app.services.pricing import precio_disponible
from app.services.sipsa_dates import CandidatoFecha, generar_candidatos, obtener_fecha_actual_colombia
from app.services.sipsa_parser import BoletinSipsa, FilaSipsa, SipsaParseError, es_producto, parse_workbook
from app.services.text_utils import normalize_basic

logger = logging.getLogger(__name__)
settings = get_settings()


def _redis_key(fecha_objetivo_iso: str) -> str:
    return f"mercoia:sipsa:{fecha_objetivo_iso}"


def _boletin_to_dict(boletin: BoletinSipsa) -> dict:
    return {
        "fecha_reporte_texto": boletin.fecha_reporte_texto,
        "filas": [dataclasses.asdict(f) for f in boletin.filas],
    }


def _dict_to_boletin(data: dict) -> BoletinSipsa:
    return BoletinSipsa(
        fecha_reporte_texto=data["fecha_reporte_texto"],
        filas=[FilaSipsa(**f) for f in data["filas"]],
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(2),
    wait=wait_fixed(1.5),
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
)
async def _descargar(client: httpx.AsyncClient, url: str) -> bytes | None:
    resp = await client.get(url, timeout=20.0)
    if resp.status_code != 200:
        return None
    return resp.content


async def _leer_cache(redis: Redis, fecha_objetivo_iso: str) -> BoletinSipsa | None:
    raw = await redis.get(_redis_key(fecha_objetivo_iso))
    if not raw:
        return None
    try:
        return _dict_to_boletin(json.loads(raw))
    except Exception:  # noqa: BLE001
        return None


async def _leer_db(db: AsyncSession, fecha_objetivo_iso: str) -> BoletinSipsa | None:
    result = await db.execute(
        select(SipsaSnapshot).where(SipsaSnapshot.fecha_objetivo_iso == fecha_objetivo_iso)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        return None
    return BoletinSipsa(
        fecha_reporte_texto=snapshot.fecha_reporte_texto,
        filas=[FilaSipsa(**f) for f in snapshot.rows],
    )


async def _guardar(
    redis: Redis, db: AsyncSession, candidato: CandidatoFecha, boletin: BoletinSipsa
) -> None:
    await redis.set(
        _redis_key(candidato.fecha_objetivo_iso),
        json.dumps(_boletin_to_dict(boletin)),
        ex=settings.sipsa_snapshot_ttl_seconds,
    )

    stmt = pg_insert(SipsaSnapshot).values(
        fecha_dane_key=candidato.fecha_dane,
        fecha_objetivo_iso=candidato.fecha_objetivo_iso,
        fecha_reporte_texto=boletin.fecha_reporte_texto,
        url_sipsa=candidato.url_sipsa,
        rows=[dataclasses.asdict(f) for f in boletin.filas],
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["fecha_dane_key"])
    await db.execute(stmt)
    await db.commit()


async def _obtener_boletin_excel(candidato: CandidatoFecha, http_client: httpx.AsyncClient) -> BoletinSipsa | None:
    """Fuente vieja: el .xlsx estático "SIPSA Diario" (ver sipsa_parser.py)."""
    try:
        contenido = await _descargar(http_client, candidato.url_sipsa)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        logger.warning("Fallo de red descargando %s: %s", candidato.url_sipsa, exc)
        return None

    if not contenido:
        return None

    try:
        return await asyncio.to_thread(parse_workbook, contenido)
    except SipsaParseError as exc:
        logger.warning("No se pudo parsear %s: %s", candidato.url_sipsa, exc)
        return None


async def _obtener_boletin_en_vivo(
    candidato: CandidatoFecha, *, redis: Redis, http_client: httpx.AsyncClient
) -> tuple[BoletinSipsa, str] | None:
    """Intenta la fuente configurada como principal (`settings.sipsa_data_source`)
    y, si no devuelve nada para este día puntual, intenta automáticamente la
    otra como respaldo -- ver el docstring del módulo. Devuelve también qué
    fuente respondió, para decidir si el resultado se puede guardar como
    definitivo (ver `_es_respaldo_provisional`)."""
    orden = ["pentaho", "excel"] if settings.sipsa_data_source == "pentaho" else ["excel", "pentaho"]

    for fuente in orden:
        if fuente == "pentaho":
            boletin = await sipsa_pentaho.obtener_boletin_pentaho(candidato, redis=redis, http_client=http_client)
        else:
            boletin = await _obtener_boletin_excel(candidato, http_client)

        if boletin:
            es_respaldo = fuente != settings.sipsa_data_source
            logger.info(
                "SIPSA %s obtenido en vivo vía %s%s",
                candidato.fecha_objetivo_iso,
                fuente,
                " (fuente de respaldo)" if es_respaldo else "",
            )
            return boletin, fuente

    return None


def _es_respaldo_provisional(candidato: CandidatoFecha, fuente: str) -> bool:
    """True si el resultado vino de la fuente de respaldo (no la configurada
    como principal) para una fecha muy reciente -- típicamente porque
    Pentaho todavía no publica los últimos días (rezago observado de 1-2
    días respecto al Excel). En ese caso NO conviene guardarlo como
    definitivo en Postgres: en unas horas la fuente principal probablemente
    ya tenga ese día con más cobertura, y no queremos congelar para siempre
    la respuesta más pobre del respaldo."""
    if fuente == settings.sipsa_data_source:
        return False
    dias_desde_hoy = (obtener_fecha_actual_colombia() - dt.date.fromisoformat(candidato.fecha_objetivo_iso)).days
    return 0 <= dias_desde_hoy <= settings.sipsa_fallback_provisional_dias


def _contar_productos(boletin: BoletinSipsa) -> int:
    return sum(1 for f in boletin.filas if es_producto(f))


async def _resolver_fuente_especifica(
    fuente: str,
    candidatos: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    minimo_productos: int = 0,
) -> tuple[BoletinSipsa, CandidatoFecha] | None:
    """Busca hacia atrás (entre `candidatos`) el día más reciente donde ESA
    fuente en particular sí tiene datos -- se usa para completar un respaldo
    provisional con lo que la fuente principal sí tenga en su propio día más
    reciente disponible (ver `_completar_con_fuente_principal`).

    Para "pentaho" primero revisa la caché/Postgres de cada día candidato
    (barato, sin red) antes de intentar una llamada en vivo: un boletín ya
    resuelto por Pentaho en un día anterior (ej. hace una semana, guardado
    para siempre en Postgres) sigue siendo perfectamente válido para esto
    aunque Pentaho esté totalmente caído *ahora mismo* -- sin esto, una
    caída de Pentaho bloqueaba la completación por completo aunque
    tuviéramos productos como "Fresa" ya guardados de un día reciente.

    `minimo_productos` descarta un día cacheado que en realidad también se
    resolvió por el respaldo (no trae MÁS que lo que ya tenemos -- un empate
    tampoco sirve, por eso se exige estrictamente más, no "al menos igual"):
    durante una caída larga, varios días recientes seguidos pueden haber
    quedado en ese mismo estado -- sin este chequeo, la búsqueda se detiene
    en el primero de esos en vez de seguir hasta uno genuinamente más
    completo. No dispara una llamada en vivo extra por descartarlo: solo
    pasa al siguiente candidato."""
    for candidato in candidatos:
        if fuente == "pentaho":
            boletin = await _leer_cache(redis, candidato.fecha_objetivo_iso)
            if not boletin:
                boletin = await _leer_db(db, candidato.fecha_objetivo_iso)
            if boletin and _contar_productos(boletin) <= minimo_productos:
                continue
            if not boletin:
                boletin = await sipsa_pentaho.obtener_boletin_pentaho(candidato, redis=redis, http_client=http_client)
        else:
            boletin = await _obtener_boletin_excel(candidato, http_client)
        if boletin:
            return boletin, candidato
    return None


async def _completar_con_fuente_principal(
    boletin: BoletinSipsa,
    candidato_usado: CandidatoFecha,
    candidatos: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> BoletinSipsa:
    """Cuando el boletín del turno vino de la fuente de respaldo (provisional,
    ver `_es_respaldo_provisional`), agrega los productos que la fuente
    PRINCIPAL sí tiene -en su propio día más reciente disponible, ya sea en
    vivo o ya guardado de antes- pero que este respaldo puntual no trae (ej.
    "Fresa" no siempre está en el Excel de un día concreto, pero sí en el
    catálogo más amplio de Pentaho). Así, un producto que solo existe en la
    fuente principal se puede seguir consultando aunque hoy se haya resuelto
    por el respaldo, incluso si Pentaho está caído en este momento.

    No se toca nada si el boletín ya viene de la fuente principal, ni para
    fechas viejas ya asentadas (`_es_respaldo_provisional` ya filtra eso
    antes de llamar aquí). Importante: se busca solo en días ESTRICTAMENTE
    anteriores a `candidato_usado` (igual que `_buscar_respaldos_mercado`) --
    si se incluyera el día de hoy, `_resolver_fuente_especifica` encontraría
    en caché el propio boletín incompleto que se está tratando de completar
    (aún no se ha guardado, pero un turno posterior sí lo vería) y se
    quedaría con eso en vez de seguir buscando hacia atrás -- bug real
    encontrado en producción, 17-sep-2026."""
    offset_usado = candidato_usado.offset_dias_solicitado or 0
    candidatos_anteriores = [
        c for c in candidatos if c.offset_dias_solicitado is not None and c.offset_dias_solicitado > offset_usado
    ]
    if not candidatos_anteriores:
        return boletin

    resultado_principal = await _resolver_fuente_especifica(
        settings.sipsa_data_source,
        candidatos_anteriores,
        redis=redis,
        db=db,
        http_client=http_client,
        minimo_productos=_contar_productos(boletin),
    )
    if not resultado_principal:
        return boletin

    boletin_principal, candidato_principal = resultado_principal
    nombres_existentes = {normalize_basic(f.nombre) for f in boletin.filas if es_producto(f)}

    siguiente_index = max((f.index for f in boletin.filas), default=0) + 1
    filas_extra = []
    for fila in boletin_principal.filas:
        if not es_producto(fila) or normalize_basic(fila.nombre) in nombres_existentes:
            continue
        # nota_fecha dice de qué día es realmente este precio (el del día más
        # reciente de la fuente principal, no necesariamente el del boletín
        # base) -- pricing.py se lo aclara al usuario en vez de dar a
        # entender que es del día del informe.
        filas_extra.append(
            dataclasses.replace(fila, index=siguiente_index, nota_fecha=candidato_principal.fecha_legible_larga)
        )
        siguiente_index += 1

    if not filas_extra:
        return boletin

    logger.info(
        "Respaldo completado con %d producto(s) que solo están en la fuente principal (%s)",
        len(filas_extra),
        settings.sipsa_data_source,
    )
    return BoletinSipsa(fecha_reporte_texto=boletin.fecha_reporte_texto, filas=[*boletin.filas, *filas_extra])


async def obtener_boletin(
    candidatos: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> tuple[BoletinSipsa, CandidatoFecha] | None:
    """Recorre los candidatos (más reciente primero) y devuelve el primer
    boletín que logre resolverse (desde caché, DB o descarga en vivo)."""

    for candidato in candidatos:
        cacheado = await _leer_cache(redis, candidato.fecha_objetivo_iso)
        if cacheado:
            logger.info("SIPSA %s resuelto desde caché Redis", candidato.fecha_objetivo_iso)
            return cacheado, candidato

        en_db = await _leer_db(db, candidato.fecha_objetivo_iso)
        if en_db:
            logger.info("SIPSA %s resuelto desde PostgreSQL", candidato.fecha_objetivo_iso)
            await redis.set(
                _redis_key(candidato.fecha_objetivo_iso),
                json.dumps(_boletin_to_dict(en_db)),
                ex=settings.sipsa_snapshot_ttl_seconds,
            )
            return en_db, candidato

        resultado_vivo = await _obtener_boletin_en_vivo(candidato, redis=redis, http_client=http_client)
        if not resultado_vivo:
            logger.debug("SIPSA no disponible aún para %s (ninguna fuente)", candidato.fecha_objetivo_iso)
            continue

        boletin, fuente = resultado_vivo
        if _es_respaldo_provisional(candidato, fuente):
            logger.info(
                "SIPSA %s vía respaldo (%s) para una fecha reciente: se cachea solo "
                "temporalmente, sin guardar como definitivo en Postgres",
                candidato.fecha_objetivo_iso,
                fuente,
            )
            boletin = await _completar_con_fuente_principal(
                boletin, candidato, candidatos, redis=redis, db=db, http_client=http_client
            )
            await redis.set(
                _redis_key(candidato.fecha_objetivo_iso),
                json.dumps(_boletin_to_dict(boletin)),
                ex=settings.sipsa_fallback_provisional_ttl_seconds,
            )
        else:
            await _guardar(redis, db, candidato, boletin)
        return boletin, candidato

    return None


async def buscar_precio_respaldo(
    nombre_normalizado: str,
    campo_precio: str,
    campo_var: str,
    candidatos_restantes: list[CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    max_intentos: int = 10,
) -> tuple[object, object, CandidatoFecha] | None:
    """Busca hacia atrás, día por día (más reciente primero), el último
    precio real disponible de `nombre_normalizado` en un mercado puntual
    (`campo_precio`/`campo_var`, ej. "precio_centroabastos"/"var_centroabastos").

    Se usa cuando el boletín de referencia del día no reportó ese mercado
    (el DANE rota qué mercados regionales incluye cada día — ver el docstring
    de sipsa_parser._encontrar_columnas_mercado). La fecha general del
    reporte que ve el agricultor no cambia por esto: solo se rellena el dato
    de ESE mercado puntual, con una nota de qué día es.

    `max_intentos` acota cuántos días se revisan como máximo, para no dejar
    la respuesta esperando minutos si hace muchos días que ese mercado no
    aparece en ningún boletín.
    """
    for candidato in candidatos_restantes[:max_intentos]:
        resultado = await obtener_boletin([candidato], redis=redis, db=db, http_client=http_client)
        if not resultado:
            continue

        boletin, _ = resultado
        for fila in boletin.filas:
            if normalize_basic(fila.nombre) != nombre_normalizado:
                continue
            precio = getattr(fila, campo_precio)
            if precio_disponible(precio):
                return precio, getattr(fila, campo_var), candidato

    return None


async def precalentar(*, redis: Redis, db: AsyncSession, http_client: httpx.AsyncClient) -> None:
    """Resuelve y cachea el boletín más reciente disponible, sin que ningún
    usuario real lo haya pedido todavía (ver punto 5 del docstring del
    módulo). Nunca propaga excepciones: se llama desde un ciclo en segundo
    plano en main.py que no debe morirse por un fallo puntual."""
    try:
        candidatos = generar_candidatos("")
        resultado = await obtener_boletin(candidatos, redis=redis, db=db, http_client=http_client)
        if resultado:
            logger.info("Precalentamiento SIPSA ok: %s", resultado[1].fecha_objetivo_iso)
        else:
            logger.warning("Precalentamiento SIPSA: no se encontró ningún boletín reciente")
    except Exception:  # noqa: BLE001
        logger.exception("Fallo inesperado precalentando la caché SIPSA (no crítico)")
