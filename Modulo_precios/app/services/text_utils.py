"""Utilidades de normalización de texto en español.

Réplica en Python de los helpers `limpiar()` / `limpiarTexto()` que existían
repetidos en varios Code nodes del flujo de n8n original.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

# Palabras "de relleno" habituales en un mensaje de consulta (fechas, cortesía,
# verbos de petición) que no aportan a identificar QUÉ producto se pregunta.
# Se comparte entre ambiguous.py (extraer la palabra de producto) y
# normalizer.py (validar que el LLM no haya alucinado un producto).
PALABRAS_RELLENO_CONSULTA = {
    "precio", "precios", "valor", "costo", "cuanto", "vale", "esta", "estan",
    "consultar", "consulta", "quiero", "quisiera", "saber", "ahora", "dime",
    "del", "de", "la", "el", "los", "las", "un", "una", "por", "favor", "me",
    "puedes", "dar", "decir", "informar", "hoy", "ayer", "anteayer", "antier",
    "antes", "hace", "dias", "dia", "dos", "tres", "cuatro", "cinco", "seis",
    "siete", "ocho", "nueve", "diez", "lunes", "martes", "miercoles", "jueves",
    "viernes", "sabado", "domingo", "pasado", "y", "en", "a", "que",
}


def strip_accents(text: str) -> str:
    """Quita tildes/diacríticos conservando la letra base (á -> a), igual que
    `.normalize("NFD").replace(/[\\u0300-\\u036f]/g, "")` en JavaScript."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


def normalize_basic(text: str | None, keep_chars: str = "") -> str:
    """Minúsculas, sin tildes, colapsa cualquier caracter que no sea
    letra/dígito/guion_bajo/espacio/ñ (ni esté en ``keep_chars``) a un espacio,
    colapsa espacios múltiples y recorta bordes.

    Equivale a:
        texto.toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "")
             .replace(/[^\\w\\sñ<keep_chars>]/g, " ").replace(/\\s+/g, " ").trim()
    """
    if not text:
        return ""

    lowered = strip_accents(str(text).lower())

    extra = re.escape(keep_chars)
    pattern = rf"[^a-z0-9_\sñ{extra}]"
    sin_simbolos = re.sub(pattern, " ", lowered)

    return re.sub(r"\s+", " ", sin_simbolos).strip()


def limpiar_nombre_producto(nombre: str | None) -> str:
    """Quita asteriscos (marca de 'variedad predominante' en SIPSA) y espacios
    duplicados, sin tocar mayúsculas/tildes (para mostrar al usuario)."""
    if not nombre:
        return ""
    sin_asteriscos = str(nombre).replace("*", "")
    return re.sub(r"\s+", " ", sin_asteriscos).strip()


def palabras_de_contenido(texto: str | None, filler: set[str] | None = None) -> list[str]:
    """Palabras normalizadas de `texto` quitando las de relleno (fechas,
    cortesía, verbos de petición) — lo que realmente identifica el producto."""
    filler = PALABRAS_RELLENO_CONSULTA if filler is None else filler
    return [p for p in normalize_basic(texto).split(" ") if p and p not in filler]


def similitud_maxima_palabras(mensaje: str | None, texto_objetivo: str | None) -> float:
    """Máxima similitud de texto (0.0 a 1.0) entre las palabras de contenido
    de `mensaje` (sin relleno) y las palabras de `texto_objetivo` (tal cual,
    sin filtrar relleno — se asume que ya es un nombre de producto).

    Se usa en dos lugares:
    - normalizer.py: red de seguridad para descartar que el LLM haya
      "alucinado" un producto que no tiene relación real con lo que el
      usuario escribió.
    - ambiguous.py: para saber si una respuesta corta del usuario
      ("negra", "tahití", "junca"...) corresponde a alguna de las opciones
      pendientes de un producto con varias variedades.
    """
    palabras_msg = palabras_de_contenido(mensaje)
    palabras_obj = [p for p in normalize_basic(texto_objetivo).split(" ") if p]

    if not palabras_msg or not palabras_obj:
        return 0.0

    mejor = 0.0
    for pm in palabras_msg:
        for po in palabras_obj:
            ratio = difflib.SequenceMatcher(None, pm, po).ratio()
            if pm in po or po in pm:
                ratio = max(ratio, 0.85)
            mejor = max(mejor, ratio)
    return mejor


def distancia_levenshtein(a: str, b: str) -> int:
    """Número mínimo de operaciones (insertar/borrar/sustituir una letra)
    para convertir `a` en `b`. Más precisa que `similitud_maxima_palabras`
    para decidir "¿esto es un typo de esa palabra?": dos palabras cortas
    pueden compartir muchas letras por pura casualidad (ej. "vanano" y
    "vaina" comparten v-a-n-a) y sacar una similitud de secuencia
    engañosamente alta sin ser typos entre sí; la distancia de edición no
    tiene ese problema."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    fila_anterior = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        fila_actual = [i]
        for j, cb in enumerate(b, start=1):
            costo_sustitucion = 0 if ca == cb else 1
            fila_actual.append(
                min(
                    fila_anterior[j] + 1,  # borrar
                    fila_actual[j - 1] + 1,  # insertar
                    fila_anterior[j - 1] + costo_sustitucion,  # sustituir
                )
            )
        fila_anterior = fila_actual
    return fila_anterior[-1]


def distancia_maxima_permitida(palabra: str) -> int:
    """Cuántos cambios se toleran para considerar algo un typo razonable de
    `palabra`. Palabras cortas (Coco, Lulo, Yuca...) toleran menos cambios
    en términos absolutos para no volverse demasiado permisivas."""
    return 1 if len(palabra) <= 4 else 2


def es_variante_razonable(mensaje: str | None, texto_objetivo: str | None) -> bool:
    """True si alguna palabra de contenido de `mensaje` está a una distancia
    de edición razonable de alguna palabra de `texto_objetivo` — es decir,
    si es plausible que sea un typo de ese texto y no una coincidencia de
    letras compartidas al azar."""
    palabras_msg = palabras_de_contenido(mensaje)
    palabras_obj = [p for p in normalize_basic(texto_objetivo).split(" ") if p]
    for pm in palabras_msg:
        for po in palabras_obj:
            if distancia_levenshtein(pm, po) <= distancia_maxima_permitida(po):
                return True
    return False
