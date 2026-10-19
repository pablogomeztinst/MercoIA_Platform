"""Clasificación del mensaje entrante: saludo sin producto, despedida o consulta.

Réplica del nodo "Code: Detectar saludo" del flujo de n8n. La rama que
ignoraba mensajes `fromMe` no aplica aquí: el webhook de WhatsApp Cloud API
sólo entrega mensajes del usuario, nunca un eco de lo que el propio bot envía
(a diferencia de Evolution API, que sí reenvía los mensajes salientes)."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.text_utils import normalize_basic

DESPEDIDAS = [
    "gracias",
    "muchas gracias",
    "mil gracias",
    "ok gracias",
    "listo gracias",
    "perfecto gracias",
    "muy amable",
    "hasta luego",
    "hasta pronto",
    "nos vemos",
    "chao",
    "chau",
    "adios",
    "bye",
    "eso era",
    "eso es todo",
    "no mas",
    "nada mas",
]

SALUDOS = [
    "hola",
    "holi",
    "holis",
    "hols",
    "hla",
    "ola",
    "olaa",
    "buenas",
    "buenos dias",
    "buen dia",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "que tal",
    "como estas",
    "como esta",
]

MENSAJES_SOLO_SALUDO = [
    "hola",
    "holi",
    "holis",
    "hols",
    "hla",
    "ola",
    "olaa",
    "buenas",
    "buenos dias",
    "buen dia",
    "buenas tardes",
    "buenas noches",
    "saludos",
    "que tal",
]

PALABRAS_RELLENO = {
    "hola", "holi", "holis", "hols", "hla", "ola", "olaa", "buenas", "buenos",
    "dias", "dia", "tardes", "noches", "saludos", "que", "tal", "como",
    "estas", "esta", "por", "favor", "gracias", "buen", "muy", "amable",
}

MENSAJE_BIENVENIDA = (
    "👋 Hola, soy la asistente de precios de MercoIA. 🌱\n\n"
    "Puedo ayudarte a consultar precios mayoristas de productos agropecuarios "
    "con base en la información disponible de SIPSA. 📊🧺\n\n"
    "Para comenzar, escribe solamente el nombre del producto que deseas "
    "consultar. 🙂\n\n"
    "Por ejemplo:\n"
    "🥔 Papa\n"
    "🍌 Banano\n"
    "🥕 Zanahoria\n"
    "🍅 Tomate\n\n"
    "📊 O escribe *resumen* para ver de una vez los 10 productos principales."
)

MENSAJE_DESPEDIDA = (
    "🙌 ¡Con gusto!\n\n"
    "Gracias por usar MercoIA. 🌱\n\n"
    "Estaré disponible cuando desees consultar nuevamente precios mayoristas "
    "de productos agropecuarios. 📊🧺\n\n"
    "¡Que tengas un excelente día! ✨"
)

# Cada respuesta de precio termina preguntando "¿Deseas consultar otro
# precio?". Sin esto, una respuesta corta como "sí" o "no" caía de largo
# como si fuera un nombre de producto y el bot contestaba con un confuso
# "no encontré ese producto". Coincidencia EXACTA con todo el mensaje (igual
# que MENSAJES_SOLO_SALUDO), no coincidencia parcial, para no atrapar por
# error un producto que legítimamente se llame "no sé qué" o similar.
MENSAJES_SOLO_NEGACION = {
    "no", "no gracias", "no por ahora", "por ahora no", "nel", "nop",
}

MENSAJES_SOLO_CONTINUAR = {
    "si", "sí", "s", "claro", "dale", "vale", "ok", "okay", "oki", "bueno",
    "otro", "otra", "otro precio", "otra consulta", "otro producto",
    "algo mas", "una mas", "uno mas", "quiero otro", "quiero otra",
}

MENSAJE_CONTINUAR = (
    "🙂 ¡Perfecto! Escríbeme el nombre del producto que quieres consultar.\n\n"
    "Por ejemplo:\n"
    "🥔 Papa\n"
    "🍌 Banano\n"
    "🥕 Zanahoria\n"
    "🍅 Tomate\n\n"
    "📊 O escribe *resumen* para ver de una vez los 10 productos principales."
)


# Pedir volver al menú inicial (ver menu.py) o el resumen de precios (ver
# resumen.py) son instrucciones de navegación, no el nombre de un producto —
# no existe ningún producto real en SIPSA llamado "menú" ni "resumen", así
# que basta con que la palabra aparezca en cualquier parte del mensaje (no
# hace falta coincidencia exacta como en MENSAJES_SOLO_SALUDO/CONTINUAR/NEGACION).
PALABRAS_MENU = {"menu"}  # normalize_basic ya quita tildes ("menú" -> "menu")
PALABRAS_RESUMEN = {"resumen"}


@dataclass
class Clasificacion:
    tipo_mensaje: str  # "volver_menu" | "resumen_precios" | "saludo_sin_producto" | "continuar_sin_producto" | "despedida" | "consulta"
    respuesta_base: str
    mensaje_limpio: str


def classify_message(mensaje_usuario: str) -> Clasificacion:
    mensaje = normalize_basic(mensaje_usuario)
    palabras_mensaje = [p for p in mensaje.split(" ") if p]

    # Máxima prioridad: son instrucciones explícitas de navegación, ganan
    # sobre cualquier otra coincidencia (saludo, despedida, etc.).
    if any(p in PALABRAS_MENU for p in palabras_mensaje):
        return Clasificacion("volver_menu", "", mensaje)

    if any(p in PALABRAS_RESUMEN for p in palabras_mensaje):
        return Clasificacion("resumen_precios", "", mensaje)

    if mensaje in MENSAJES_SOLO_NEGACION:
        return Clasificacion("despedida", MENSAJE_DESPEDIDA, mensaje)

    if mensaje in MENSAJES_SOLO_CONTINUAR:
        return Clasificacion("continuar_sin_producto", MENSAJE_CONTINUAR, mensaje)

    es_despedida = any(normalize_basic(d) in mensaje for d in DESPEDIDAS)

    contiene_saludo = any(normalize_basic(s) in mensaje for s in SALUDOS)
    palabras_importantes = [p for p in palabras_mensaje if p not in PALABRAS_RELLENO]
    es_mensaje_solo_saludo = mensaje in MENSAJES_SOLO_SALUDO

    es_saludo_sin_producto = (not es_despedida) and (
        es_mensaje_solo_saludo or (contiene_saludo and not palabras_importantes)
    )

    if es_despedida:
        return Clasificacion("despedida", MENSAJE_DESPEDIDA, mensaje)

    if es_saludo_sin_producto:
        return Clasificacion("saludo_sin_producto", MENSAJE_BIENVENIDA, mensaje)

    return Clasificacion("consulta", "", mensaje)
