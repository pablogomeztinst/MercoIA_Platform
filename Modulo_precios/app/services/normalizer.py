"""Normalizador de nombres de producto vía LLM.

Réplica del nodo "Agente Normalizador" del flujo de n8n: dada la lista de
productos disponibles en el boletín del día y el mensaje del usuario, el
modelo decide cuál producto de la lista oficial quiso escribir (corrigiendo
errores ortográficos, apodos regionales, etc.) sin inventar productos que no
estén en la lista ni responder precios (esa parte la hace pricing.py con el
dato real, para evitar alucinaciones)."""
from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.text_utils import (
    distancia_levenshtein,
    distancia_maxima_permitida,
    es_variante_razonable,
    normalize_basic,
    palabras_de_contenido,
    similitud_maxima_palabras,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


SYSTEM_PROMPT = """\
Eres un normalizador de nombres de productos agrícolas para MercoIA.

Tu única tarea es escoger cuál producto de la lista oficial quiso escribir el usuario.

Reglas obligatorias:
1. Debes escoger únicamente un producto que exista en la lista oficial.
2. No inventes productos.
3. No respondas precios.
4. No expliques nada.
5. Responde únicamente en formato JSON válido.
6. No uses markdown.
7. Si el usuario escribe un producto con errores ortográficos, corrígelo usando la lista oficial.
8. Si el usuario escribe "aullama", "auyama" o algo parecido, probablemente se refiere a "Ahuyama", si existe en la lista.
9. Si el usuario escribe "sanahoria", probablemente se refiere a "Zanahoria", si existe en la lista.
10. Si el usuario escribe "sebolla", probablemente se refiere a algún producto que empiece por "Cebolla", si existe en la lista.
11. Si el usuario escribe "papa", NO debe confundirse con "Papaya".
12. Si el usuario escribe "papa negra", debes escoger "Papa negra" si existe en la lista.
13. Si el usuario escribe "papa criolla", debes escoger "Papa criolla" si existe en la lista.
14. Si el usuario escribe "plátano guineo" o "platano guineo", debes escoger "Plátano guineo" si existe en la lista.
15. Si el usuario escribe "plátano hartón verde", "platano harton verde", "hartón verde" o "harton verde", debes escoger "Plátano hartón verde" si existe en la lista.
16. Si el usuario escribe una variedad específica que NO existe en la lista oficial, NO la reemplaces silenciosamente por una opción genérica.
17. Por ejemplo, si el usuario escribe "tomate cherry" y en la lista solo existe "Tomate", responde encontrado=false.
18. Por ejemplo, si el usuario escribe "papa pastusa" o "papa pastosa" y en la lista solo existen "Papa negra" y "Papa criolla", responde encontrado=false, salvo que el mensaje ya incluya claramente "negra" o "criolla".
19. Si hay varios productos posibles, escoge el más específico y más cercano al texto del usuario.
20. La fecha del mensaje, como "ayer", "anteayer", "martes 16" o "hace dos días", no debe afectar la selección del producto.

El formato exacto de respuesta debe ser:
{"encontrado": true, "x": 0, "nombre_escogido": "Nombre exacto del producto", "confianza": 0.95}

Si no encuentras una coincidencia razonable, responde:
{"encontrado": false, "x": -1, "nombre_escogido": null, "confianza": 0}
"""


# ---------- Red de seguridad contra alucinaciones del modelo ----------
# gpt-4.1-nano es barato y rápido, pero a veces "adivina" un producto de la
# lista en vez de reconocer que ninguno corresponde (ej. preguntó por
# "uchuva" —que ese día no está en el boletín— y el modelo respondió
# confiadamente "Mandarina", en lugar de encontrado=false). Como defensa
# adicional a las reglas del prompt, se valida que el nombre elegido por el
# modelo tenga una similitud de texto razonable con alguna palabra relevante
# del mensaje del usuario; si no, se descarta la respuesta como si no hubiera
# encontrado nada. El umbral se calibró contra casos reales (typos como
# "sanahoria"/"zanahoria" o "sebolla"/"cebolla" pasan con margen; productos
# no relacionados como "uchuva"/"ahuyama" o "fresa"/"tomate" quedan
# claramente por debajo).
_UMBRAL_SIMILITUD = 0.6


def _extraer_json(texto: str) -> dict | None:
    limpio = texto.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", limpio)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


_RESULTADO_NO_ENCONTRADO = {
    "encontrado": False,
    "x": -1,
    "nombre_escogido": None,
    "confianza": 0,
    "sugerencias": [],
}


def _resultado_no_encontrado(mensaje_usuario: str, coincidencias: list[tuple[dict, float]]) -> dict:
    resultado = dict(_RESULTADO_NO_ENCONTRADO)
    resultado["sugerencias"] = _construir_sugerencias(mensaje_usuario, coincidencias)
    return resultado


# ---------- Pistas de coincidencia aproximada (typos) ----------
# gpt-4.1-nano corrige bien los typos que el prompt ejemplifica explícitamente
# (rules 8-10), pero generaliza mal los que no — ej. "mansana" en vez de
# "manzana" (un error ortográfico común y esperable entre campesinos
# escribiendo desde el celular) a veces no lo relaciona con ningún producto y
# responde encontrado=false. En vez de agregar una regla nueva por cada typo
# posible (no escala), se calcula la similitud de texto contra TODA la lista
# de productos con código determinístico (rápido y confiable) y se le pasan
# al modelo los 3 más parecidos como sugerencia — su trabajo deja de ser
# "adivinar la corrección" y pasa a ser "confirmar cuál de estas pistas es la
# correcta", que es una tarea mucho más fácil para un modelo pequeño.
_TOP_N_SUGERENCIAS = 3
_UMBRAL_SUGERENCIA_MINIMA = 0.4  # por debajo de esto ni se sugiere, para no meter ruido


def _mejores_coincidencias(mensaje_usuario: str, productos: list[dict]) -> list[tuple[dict, float]]:
    puntuados = [
        (p, similitud_maxima_palabras(mensaje_usuario, p.get("nombre", "")))
        for p in productos
    ]
    puntuados.sort(key=lambda par: par[1], reverse=True)
    return [par for par in puntuados[:_TOP_N_SUGERENCIAS] if par[1] >= _UMBRAL_SUGERENCIA_MINIMA]


def _bloque_sugerencias(coincidencias: list[tuple[dict, float]]) -> str:
    if not coincidencias:
        return "Ninguna sugerencia automática superó el umbral mínimo."
    lineas = [
        f'- x={p["x"]}, "{p["nombre"]}" (similitud de texto: {score:.2f})'
        for p, score in coincidencias
    ]
    return "\n".join(lineas)


# ---------- Respaldo determinístico cuando el modelo dice "no encontrado" ----------
# Solo se activa para nombres de UNA sola palabra (Coco, Lulo, Zanahoria,
# Guayaba...), nunca para nombres compuestos (Papa X, Cebolla X, Limón X,
# Plátano X, Manzana X): ahí "papa pastusa" da similitud 1.0 contra "Papa
# negra" Y "Papa criolla" a la vez (la palabra compartida "papa" domina el
# puntaje), así que decidir solo por similitud sería incorrecto — esos casos
# quedan enteramente al criterio semántico del modelo.
#
# Para nombres simples, la primera versión comparaba el candidato top contra
# el segundo mejor candidato ("¿hay suficiente margen entre ellos?"). Se
# descartó: "vanano" (typo de "banano") da 0.83 de similitud con "Banano" —
# correcto — pero también 0.73 con "vaina" (de "Arveja verde en vaina"),
# solo por casualidad de letras compartidas (v-a-n-a), y ese margen escaso
# bloqueaba un rescate que en realidad era obvio. La comparación correcta no
# es "top candidato vs. segundo candidato" sino "¿qué tan pocas letras hay
# que cambiarle al mensaje para llegar exactamente a este nombre?" —eso es
# distancia de edición (Levenshtein), una medida mucho más específica para
# typos que la similitud general de secuencia.
_UMBRAL_RESCATE = 0.8


def _intentar_rescate_por_similitud(mensaje_usuario: str, coincidencias: list[tuple[dict, float]]) -> dict | None:
    if not coincidencias:
        return None

    mejor, score_mejor = coincidencias[0]
    if score_mejor < _UMBRAL_RESCATE:
        return None

    nombre_candidato = normalize_basic(mejor.get("nombre", ""))
    if len(nombre_candidato.split(" ")) != 1:
        return None  # nombre compuesto: se deja enteramente al modelo

    palabras_msg = palabras_de_contenido(mensaje_usuario)
    if not palabras_msg:
        return None

    distancia_minima = min(distancia_levenshtein(p, nombre_candidato) for p in palabras_msg)
    if distancia_minima > distancia_maxima_permitida(nombre_candidato):
        return None

    return {
        "encontrado": True,
        "x": mejor["x"],
        "nombre_escogido": mejor["nombre"],
        "confianza": round(score_mejor, 2),
    }


# ---------- Última red: "¿quisiste decir...?" ----------
# Si ni el modelo ni el rescate automático lograron identificar el producto,
# antes de rendirnos del todo revisamos si alguno de los candidatos por
# similitud es una variante ortográfica razonable (misma exigencia de
# distancia de edición que el rescate automático, palabra por palabra, para
# no sugerir productos que solo comparten letras por casualidad como pasaba
# con "vanano"/"vaina"). A diferencia del rescate, aquí SÍ se permiten
# nombres compuestos (Papa negra, Cebolla junca...) porque el usuario tiene
# que confirmar — no se asume nada automáticamente.
_MAX_SUGERENCIAS = 2


def _construir_sugerencias(mensaje_usuario: str, coincidencias: list[tuple[dict, float]]) -> list[dict]:
    sugerencias = []
    for producto, _score in coincidencias:
        if es_variante_razonable(mensaje_usuario, producto.get("nombre", "")):
            sugerencias.append({"x": producto["x"], "nombre": producto["nombre"]})
        if len(sugerencias) >= _MAX_SUGERENCIAS:
            break
    return sugerencias


async def _llamar_modelo(messages: list[dict]) -> dict | None:
    """Una llamada al modelo normalizador. `None` si falló la llamada o la
    respuesta no fue un JSON válido con la forma esperada."""
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        crudo = response.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        logger.exception("Error llamando al modelo normalizador de OpenAI")
        return None

    parseado = _extraer_json(crudo)
    if not parseado or "encontrado" not in parseado:
        logger.warning("Respuesta del normalizador no es JSON válido: %r", crudo)
        return None
    return parseado


async def normalizar_producto(mensaje_usuario: str, productos: list[dict]) -> dict:
    """Devuelve {"encontrado", "x", "nombre_escogido", "confianza"}.

    Deliberadamente SIN historial de turnos anteriores: cada consulta se
    normaliza de forma aislada contra la lista de productos del boletín
    vigente. Pasarle al modelo las últimas preguntas/respuestas (como hacía
    la "Simple Memory" del flujo n8n original) causaba que preguntas por
    productos que no existen en el boletín (ej. "uchuva") se contestaran con
    el último producto válido de un turno anterior (ej. "mandarina"), porque
    el modelo se anclaba a su propia respuesta previa en vez de reconocer
    que el nuevo producto no está en la lista. Ver conversation.py para la
    desambiguación multi-turno legítima (papa negra/criolla, etc.), que se
    resuelve aparte y no necesita memoria del LLM."""
    if not productos:
        return dict(_RESULTADO_NO_ENCONTRADO)

    coincidencias = _mejores_coincidencias(mensaje_usuario, productos)

    # Se le manda al modelo el mensaje en minúsculas: se detectó que gpt-4.1-nano
    # es sorprendentemente sensible al patrón "Primera letra mayúscula, resto
    # minúscula" (justo como autocapitaliza el teclado de cualquier celular al
    # iniciar un mensaje) — "Mansana" fallaba mientras "mansana", "MANSANA" y
    # "mansana " funcionaban bien, con la MISMA lista de productos y la MISMA
    # sugerencia por similitud. La mayúscula no aporta ninguna señal semántica
    # para esta tarea, así que se elimina esa fuente de fragilidad del modelo.
    mensaje_para_modelo = mensaje_usuario.lower()

    user_content = (
        f"Mensaje del usuario:\n{mensaje_para_modelo}\n\n"
        f"Lista oficial de productos disponibles:\n{json.dumps(productos, ensure_ascii=False)}\n\n"
        "Sugerencias automáticas por similitud de texto (pueden tener errores, "
        "verifícalas contra las reglas y el mensaje antes de usarlas; si ninguna "
        "encaja de verdad, ignóralas y responde encontrado=false):\n"
        f"{_bloque_sugerencias(coincidencias)}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    parseado = await _llamar_modelo(messages)

    # gpt-4.1-nano no es perfectamente consistente entre llamadas idénticas
    # incluso con temperature=0 (falla ocasional y no reproducible, ya
    # descartamos que fuera un problema de nuestros datos o del prompt). Si
    # falló y había alguna sugerencia real disponible, vale la pena
    # reintentar una vez antes de decirle al agricultor que no existe.
    if (not parseado or not parseado.get("encontrado")) and coincidencias:
        logger.info(
            "Normalizador sin resultado en el primer intento, reintentando una vez: mensaje=%r",
            mensaje_usuario,
        )
        parseado = await _llamar_modelo(messages) or parseado

    if not parseado:
        return dict(_RESULTADO_NO_ENCONTRADO)

    if parseado.get("encontrado") and parseado.get("nombre_escogido"):
        similitud = similitud_maxima_palabras(mensaje_usuario, str(parseado["nombre_escogido"]))
        if similitud < _UMBRAL_SIMILITUD:
            logger.warning(
                "Normalizador descartado por baja similitud (%.2f < %.2f): "
                "mensaje=%r -> nombre_escogido=%r",
                similitud,
                _UMBRAL_SIMILITUD,
                mensaje_usuario,
                parseado["nombre_escogido"],
            )
            return _resultado_no_encontrado(mensaje_usuario, coincidencias)
        return parseado

    # El modelo dijo "no encontrado" — antes de rendirse, intentamos el
    # respaldo determinístico para typos claros de nombres simples.
    rescate = _intentar_rescate_por_similitud(mensaje_usuario, coincidencias)
    if rescate:
        logger.info(
            "Normalizador rescatado por similitud alta y sin ambigüedad: "
            "mensaje=%r -> nombre_escogido=%r (similitud=%.2f)",
            mensaje_usuario,
            rescate["nombre_escogido"],
            rescate["confianza"],
        )
        return rescate

    return _resultado_no_encontrado(mensaje_usuario, coincidencias)
