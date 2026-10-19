"""Orquestador de la conversación.

Es la evolución del grafo del flujo de n8n original, con dos diferencias
importantes respecto a la v1 (ver ambiguous.py para el detalle), más el menú
inicial que se agregó después (ver menu.py):

    Webhook -> Variables ->
        0. ¿Había un menú pendiente del turno anterior? (Redis)
              -> "2"/enfermedades -> Send Text - aún no disponible
              -> "1"/precios      -> Send Text - confirmación, escribe el producto
              -> cualquier otra cosa -> sigue el flujo normal (ver abajo)
        Code: Detectar saludo -> Switch
        -> saludo_sin_producto, "volver_menu" (pidió explícitamente el menú,
           ej. "quiero ir al menú inicial", en cualquier punto de la
           conversación), o primer contacto de este teléfono (sin registros
           en conversation_logs) -> Send Text - menú (1. precios / 2.
           enfermedades). El menú se repite cada vez que el usuario "empieza"
           una conversación (saluda) o lo pide, no solo la primera vez, para
           que siempre pueda elegir/regresar al módulo que quiere usar.
        -> despedida           -> Send Text - despedida
        -> resumen_precios (pidió explícitamente "resumen", en cualquier
           punto de la conversación) -> obtener boletín -> Send Text con los
           N=10 productos principales en un solo mensaje (ver resumen.py),
           sin pasar por el normalizador ni la desambiguación
        -> consulta:
              A. ¿Responde una desambiguación pendiente? (Redis, sin red)
              A2. Si no, ¿responde un "¿quisiste decir...?" pendiente? (Redis)
              B. Generar URLs SIPSA candidatas -> obtener boletín
                    -> falla todo -> Send Text - no se encontró base reciente
              C. ¿El mensaje menciona un producto con varias variedades HOY
                 (calculado contra el boletín real, no hardcodeado como en
                 la v1 que solo sabía de papa/plátano)? -> Send Text - aclarar
              D. Agente Normalizador (ver normalizer.py: pistas de similitud,
                 reintento, rescate por distancia de edición)
              D2. Si aun así no se encontró pero hay una variante ortográfica
                 razonable (ver suggestions.py) -> Send Text - "¿quisiste decir?"
              E. Respaldo de mercado si falta -> Code: obtener precios reales
                 -> Send Text
"""
from __future__ import annotations

import logging

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationLog
from app.services import (
    ambiguous,
    classifier,
    menu,
    normalizer,
    pricing,
    resumen,
    sipsa_dates,
    sipsa_fetch,
    sipsa_parser,
    suggestions,
)
from app.services.text_utils import normalize_basic
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)

MENSAJE_ERROR_INESPERADO = (
    "😕 Tuve un problema procesando tu mensaje. Por favor intenta nuevamente en unos minutos."
)

MENSAJE_TIPO_NO_SOPORTADO = (
    "Por ahora solo puedo leer mensajes de texto. ✍️\n\n"
    "Escríbeme el nombre del producto que quieres consultar, por ejemplo: *papa*, *tomate* o *plátano*."
)


async def _log(db: AsyncSession, phone: str, direction: str, message_type: str, content: str) -> None:
    try:
        db.add(ConversationLog(phone=phone, direction=direction, message_type=message_type, content=content))
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el log de conversación (no es crítico)")
        await db.rollback()


async def _enviar_sin_base(
    wa: WhatsAppClient, db: AsyncSession, phone: str, candidatos: list[sipsa_dates.CandidatoFecha]
) -> None:
    msg = _mensaje_sin_base(candidatos)
    await wa.send_text(phone, msg)
    await _log(db, phone, "out", "sin_base_reciente", msg)


def _mensaje_sin_base(candidatos: list[sipsa_dates.CandidatoFecha]) -> str:
    primero = candidatos[0] if candidatos else None
    if primero and primero.fecha_solicitada:
        fecha_txt = primero.fecha_legible_larga or primero.fecha_legible
        return (
            f"⚠️ No encontré una publicación SIPSA Diario para la fecha solicitada: {fecha_txt}.\n\n"
            "Esto puede ocurrir porque ese día fue fin de semana, festivo o porque la fuente oficial "
            "no publicó base diaria para esa fecha.\n\n"
            "Puedes intentar con otra fecha o consultar el precio más reciente disponible. 🙂"
        )
    return (
        "Hola, soy asistente de MercoIA. 🌱\n\n"
        "No pude encontrar una base SIPSA Diario reciente para consultar precios.\n\n"
        "Esto puede ocurrir porque:\n"
        "- No hay publicaciones disponibles en los últimos días consultados.\n"
        "- La fuente oficial del DANE cambió temporalmente la ruta del archivo.\n"
        "- Hubo un problema de conexión con la fuente oficial.\n\n"
        "Por favor intenta consultar nuevamente más tarde."
    )


def _resolver_fila(normalizado: dict | None, filas: list[sipsa_parser.FilaSipsa]) -> sipsa_parser.FilaSipsa | None:
    """Replica la validación de `x` que hace pricing.build_pricing_response,
    para saber si vale la pena buscar precios de respaldo por mercado."""
    if not normalizado or not normalizado.get("encontrado"):
        return None
    try:
        x = int(normalizado.get("x", -1))
    except (TypeError, ValueError):
        return None
    if x < 0 or x >= len(filas):
        return None
    return filas[x]


async def _buscar_respaldos_mercado(
    fila_objetivo: sipsa_parser.FilaSipsa,
    candidato_usado: sipsa_dates.CandidatoFecha,
    candidatos: list[sipsa_dates.CandidatoFecha],
    *,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> tuple[tuple | None, tuple | None]:
    """Para cada mercado que el boletín de referencia no reportó ese día
    puntual (ver sipsa_parser: Bucaramanga es opcional), busca hacia atrás
    el precio real más reciente de ese mismo producto en ese mercado.
    Devuelve (respaldo_centroabastos, respaldo_corabastos), cada uno como
    `(precio, var, fecha_legible_larga)` o `None` si no hizo falta / no se
    encontró nada dentro de la ventana de búsqueda."""
    nombre_normalizado = normalize_basic(fila_objetivo.nombre)
    offset_usado = candidato_usado.offset_dias_solicitado or 0
    candidatos_restantes = [
        c
        for c in candidatos
        if c.offset_dias_solicitado is not None and c.offset_dias_solicitado > offset_usado
    ]

    async def _buscar(campo_precio: str, campo_var: str) -> tuple | None:
        if not candidatos_restantes:
            return None
        encontrado = await sipsa_fetch.buscar_precio_respaldo(
            nombre_normalizado,
            campo_precio,
            campo_var,
            candidatos_restantes,
            redis=redis,
            db=db,
            http_client=http_client,
        )
        if not encontrado:
            return None
        precio, var, candidato_fuente = encontrado
        return precio, var, candidato_fuente.fecha_legible_larga

    respaldo_centro = None
    if not pricing.precio_disponible(fila_objetivo.precio_centroabastos):
        respaldo_centro = await _buscar("precio_centroabastos", "var_centroabastos")

    respaldo_cora = None
    if not pricing.precio_disponible(fila_objetivo.precio_corabastos):
        respaldo_cora = await _buscar("precio_corabastos", "var_corabastos")

    return respaldo_centro, respaldo_cora


async def procesar_mensaje(
    *,
    phone: str,
    mensaje_usuario: str,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
) -> None:
    """Punto de entrada seguro: nunca deja al usuario sin respuesta ni
    propaga excepciones hacia el webhook."""
    wa = WhatsAppClient(http_client)
    try:
        await _procesar_mensaje_interno(
            phone=phone, mensaje_usuario=mensaje_usuario, redis=redis, db=db, http_client=http_client, wa=wa
        )
    except Exception:  # noqa: BLE001
        logger.exception("Error inesperado procesando mensaje de %s", phone)
        await wa.send_text(phone, MENSAJE_ERROR_INESPERADO)
        await _log(db, phone, "out", "error", MENSAJE_ERROR_INESPERADO)


async def _procesar_mensaje_interno(
    *,
    phone: str,
    mensaje_usuario: str,
    redis: Redis,
    db: AsyncSession,
    http_client: httpx.AsyncClient,
    wa: WhatsAppClient,
) -> None:
    # Debe consultarse ANTES de loguear el mensaje actual, o ya no se vería
    # como "primera vez" (se usa más abajo para mostrar el menú también en
    # el caso de que el primer mensaje de siempre no sea un saludo).
    es_primera_vez = await menu.es_primer_contacto(db, phone)
    await _log(db, phone, "in", "text", mensaje_usuario)

    # Paso 0: ¿este mensaje responde a un menú pendiente del turno anterior?
    # Tiene prioridad sobre todo lo demás: si se le acaba de preguntar 1 o 2,
    # lo que responda ahora es la respuesta a esa pregunta.
    resolucion_menu = await menu.resolver_pendiente(redis, phone, mensaje_usuario)
    if resolucion_menu.tipo == "enfermedades":
        await wa.send_text(phone, menu.MENSAJE_ENFERMEDADES_NO_DISPONIBLE)
        await _log(db, phone, "out", "enfermedades_no_disponible", menu.MENSAJE_ENFERMEDADES_NO_DISPONIBLE)
        return
    if resolucion_menu.tipo == "precios_confirmado":
        await wa.send_text(phone, menu.MENSAJE_PRECIOS_CONFIRMACION)
        await _log(db, phone, "out", "precios_confirmado", menu.MENSAJE_PRECIOS_CONFIRMACION)
        return
    # "precios_implicito" (ignoró el menú y escribió otra cosa, ej. ya
    # escribió el producto) o "no_aplica" (no había menú pendiente): el
    # mensaje sigue tal cual por el flujo normal de siempre.

    clasificacion = classifier.classify_message(mensaje_usuario)

    # Paso 0b: se muestra el menú cada vez que el usuario "empieza" una
    # conversación —es decir, cuando saluda sin mencionar un producto, o
    # pide explícitamente volver al menú (ej. "quiero ir al menú inicial")—,
    # no solo la primera vez en la vida. También se muestra si es
    # literalmente el primer mensaje de siempre de este teléfono aunque no
    # sea un saludo (ej. escribió el nombre de un producto de una), para que
    # todo mundo pase por el menú al menos una vez.
    if clasificacion.tipo_mensaje in ("saludo_sin_producto", "volver_menu") or es_primera_vez:
        # Se descarta cualquier desambiguación o sugerencia pendiente: si el
        # usuario saltó al menú a mitad de camino, un mensaje posterior sin
        # relación ya no debe interpretarse como la respuesta a esa pregunta
        # vieja (ej. responder "1" del menú no debe leerse como "papa negra").
        await ambiguous.olvidar_pendiente(redis, phone)
        await suggestions.olvidar_pendiente(redis, phone)
        await menu.marcar_menu_pendiente(redis, phone)
        await wa.send_text(phone, menu.MENSAJE_MENU_INICIAL)
        await _log(db, phone, "out", "menu_inicial", menu.MENSAJE_MENU_INICIAL)
        return

    if clasificacion.tipo_mensaje in ("continuar_sin_producto", "despedida"):
        await wa.send_text(phone, clasificacion.respuesta_base)
        await _log(db, phone, "out", clasificacion.tipo_mensaje, clasificacion.respuesta_base)
        return

    # ---------- tipo_mensaje == "resumen_precios" ----------
    # Atajo pedido explícitamente ("resumen"): un solo mensaje con los N=10
    # productos principales (ver resumen.py), sin pasar por el normalizador
    # ni la desambiguación -- ya se sabe de antemano qué productos mostrar.
    if clasificacion.tipo_mensaje == "resumen_precios":
        candidatos = sipsa_dates.generar_candidatos(mensaje_usuario)
        resultado = await sipsa_fetch.obtener_boletin(candidatos, redis=redis, db=db, http_client=http_client)
        if not resultado:
            await _enviar_sin_base(wa, db, phone, candidatos)
            return

        boletin, candidato_usado = resultado
        respuesta = await resumen.construir_resumen(
            boletin.filas, candidato_usado, candidatos, redis=redis, db=db, http_client=http_client
        )
        await wa.send_text(phone, respuesta)
        await _log(db, phone, "out", "resumen_precios", respuesta)
        return

    # ---------- tipo_mensaje == "consulta" ----------
    # Paso A: ¿había una pregunta de desambiguación pendiente de un turno
    # anterior (ej. "¿papa negra o criolla?") que este mensaje responde?
    # Solo necesita Redis, no hace falta bajar el boletín todavía.
    resolucion = await ambiguous.resolver_pendiente(redis, phone, mensaje_usuario)
    mensaje_efectivo = resolucion.mensaje_usuario
    resuelto_desde_contexto = resolucion.resuelto

    # Paso A2: si no era una desambiguación, ¿responde a un "¿quisiste decir
    #...?" del turno anterior (ver suggestions.py)? También solo Redis.
    if not resuelto_desde_contexto:
        resolucion_sugerencia = await suggestions.resolver_pendiente(redis, phone, mensaje_usuario)
        mensaje_efectivo = resolucion_sugerencia.mensaje_usuario
        resuelto_desde_contexto = resolucion_sugerencia.resuelto

    candidatos = sipsa_dates.generar_candidatos(mensaje_efectivo)
    resultado = await sipsa_fetch.obtener_boletin(candidatos, redis=redis, db=db, http_client=http_client)

    if not resultado:
        await _enviar_sin_base(wa, db, phone, candidatos)
        return

    boletin, candidato_usado = resultado
    productos = sipsa_parser.construir_lista_productos(boletin.filas)

    # Paso B: ¿el mensaje (ya resuelto o no) menciona un producto con varias
    # variedades HOY (ej. limón común/Tahití) sin especificar cuál? Esto se
    # calcula contra la lista real del boletín vigente, no contra nombres
    # hardcodeados, así que cubre cualquier producto que el DANE divida en
    # variedades ese día.
    if not resuelto_desde_contexto:
        grupo_ambiguo = ambiguous.detectar_ambiguedad(mensaje_efectivo, productos)
        if grupo_ambiguo:
            await ambiguous.guardar_contexto(redis, phone, grupo_ambiguo, mensaje_efectivo)
            respuesta = ambiguous.construir_respuesta_ambigua(grupo_ambiguo)
            await wa.send_text(phone, respuesta, delay=0)
            await _log(db, phone, "out", "aclarar_producto", respuesta)
            return
    # Sin historial de turnos anteriores a propósito: se probó pasarle al LLM las
    # últimas preguntas/respuestas (igual que la "Simple Memory" del flujo n8n) y
    # causaba que preguntas por productos que NO existen en el boletín (ej.
    # "uchuva", "fresa") se contestaran con el último producto válido de un turno
    # anterior (ej. "mandarina"), porque el modelo se anclaba a su propia
    # respuesta previa en vez de reconocer que el nuevo producto no está en la
    # lista. La desambiguación multi-turno legítima (papa negra/criolla, plátano
    # guineo/hartón) no depende de esto: ya se resuelve aparte en ambiguous.py,
    # reconstruyendo el mensaje efectivo de forma determinística.
    normalizado = await normalizer.normalizar_producto(mensaje_efectivo, productos)

    # Paso D2: último recurso antes de decir "no existe" — si el producto
    # está mal escrito pero SÍ se registró algo parecido de verdad (no ruido:
    # ver normalizer._es_variante_razonable), se le pregunta al usuario si
    # se refería a eso, en vez de responder directamente que no se encontró.
    if not normalizado.get("encontrado") and normalizado.get("sugerencias"):
        await suggestions.guardar_contexto(redis, phone, mensaje_efectivo, normalizado["sugerencias"])
        respuesta = suggestions.construir_mensaje_sugerencias(normalizado["sugerencias"])
        await wa.send_text(phone, respuesta, delay=0)
        await _log(db, phone, "out", "sugerir_producto", respuesta)
        return

    respaldo_centroabastos = respaldo_corabastos = None
    fila_objetivo = _resolver_fila(normalizado, boletin.filas)

    if fila_objetivo and candidato_usado.modo_busqueda_fecha == "mas_reciente":
        respaldo_centroabastos, respaldo_corabastos = await _buscar_respaldos_mercado(
            fila_objetivo, candidato_usado, candidatos, redis=redis, db=db, http_client=http_client
        )

    resultado_precio = pricing.build_pricing_response(
        normalizado,
        boletin.filas,
        candidato_usado,
        respaldo_centroabastos=respaldo_centroabastos,
        respaldo_corabastos=respaldo_corabastos,
    )

    await wa.send_text(phone, resultado_precio.respuesta_base)
    await _log(
        db,
        phone,
        "out",
        "precio" if resultado_precio.encontrado else "producto_no_encontrado",
        resultado_precio.respuesta_base,
    )
