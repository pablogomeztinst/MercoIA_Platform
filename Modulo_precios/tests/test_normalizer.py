"""Tests de la red de seguridad contra alucinaciones del normalizador, y del
respaldo determinístico contra typos (ver _intentar_rescate_por_similitud).
Usa app.services.text_utils.similitud_maxima_palabras (compartida con
ambiguous.py). No se testea normalizar_producto() en sí porque llama a la
API real de OpenAI; esto cubre la parte determinística."""
from app.services.normalizer import (
    _UMBRAL_SIMILITUD,
    _construir_sugerencias,
    _intentar_rescate_por_similitud,
    _mejores_coincidencias,
    _resultado_no_encontrado,
)
from app.services.text_utils import es_variante_razonable as _es_variante_razonable
from app.services.text_utils import similitud_maxima_palabras as _similitud_maxima

PRODUCTOS = [
    {"x": 5, "nombre": "Coco"},
    {"x": 8, "nombre": "Banano*"},
    {"x": 9, "nombre": "Arveja verde en vaina"},
    {"x": 12, "nombre": "Mandarina*"},
    {"x": 20, "nombre": "Manzana royal gala"},
    {"x": 37, "nombre": "Papa negra*"},
    {"x": 38, "nombre": "Papa  criolla"},
]


def test_coincidencia_exacta():
    assert _similitud_maxima("mandarina", "Mandarina*") >= _UMBRAL_SIMILITUD


def test_typos_comunes_pasan_el_umbral():
    assert _similitud_maxima("sanahoria", "Zanahoria") >= _UMBRAL_SIMILITUD
    assert _similitud_maxima("sebolla", "Cebolla cabezona blanca") >= _UMBRAL_SIMILITUD
    assert _similitud_maxima("auyama", "Ahuyama*") >= _UMBRAL_SIMILITUD
    assert _similitud_maxima("aullama", "Ahuyama*") >= _UMBRAL_SIMILITUD


def test_frase_completa_con_relleno():
    assert (
        _similitud_maxima("precio de la papa criolla ayer", "Papa  criolla")
        >= _UMBRAL_SIMILITUD
    )


def test_producto_no_relacionado_no_pasa_el_umbral():
    # Caso real que causó el bug reportado: el modelo confundió "uchuva" con
    # "mandarina" porque ese día no había uchuva en el boletín.
    assert _similitud_maxima("uchuva", "Mandarina*") < _UMBRAL_SIMILITUD
    assert _similitud_maxima("ahora quiero saber el precio de la uchuva", "Mandarina*") < _UMBRAL_SIMILITUD
    assert _similitud_maxima("fresa", "Tomate*") < _UMBRAL_SIMILITUD
    assert _similitud_maxima("uva", "Naranja*") < _UMBRAL_SIMILITUD


def test_mensaje_o_nombre_vacio_da_similitud_cero():
    assert _similitud_maxima("", "Papa negra*") == 0.0
    assert _similitud_maxima("papa", "") == 0.0


# ---------- Sugerencias automáticas + respaldo determinístico contra typos ----------


def test_mejores_coincidencias_ordena_por_similitud():
    coincidencias = _mejores_coincidencias("mansana", PRODUCTOS)
    assert coincidencias[0][0]["nombre"] == "Manzana royal gala"


def test_rescate_typo_simple_de_una_palabra():
    # "madarina" (typo de "mandarina", falta una "n") es un nombre de UNA
    # sola palabra en la lista real -> se puede rescatar con seguridad sin
    # depender de que el modelo lo adivine.
    coincidencias = _mejores_coincidencias("madarina", PRODUCTOS)
    rescate = _intentar_rescate_por_similitud("madarina", coincidencias)
    assert rescate is not None
    assert rescate["encontrado"] is True
    assert rescate["nombre_escogido"] == "Mandarina*"
    assert rescate["x"] == 12


def test_rescate_no_se_confunde_por_letras_compartidas_al_azar():
    # Caso real reportado: "vanano" (typo de "banano", una sola letra
    # distinta) también se parece por casualidad a "vaina" (de "Arveja
    # verde en vaina") solo por compartir letras v-a-n-a — un enfoque basado
    # en "margen sobre el segundo candidato" bloqueaba el rescate por esto.
    # La distancia de edición (Levenshtein) evita el problema: "vanano" está
    # a 1 cambio de "banano" y a varios más de "vaina".
    coincidencias = _mejores_coincidencias("vanano", PRODUCTOS)
    assert coincidencias[0][0]["nombre"] == "Banano*"
    rescate = _intentar_rescate_por_similitud("vanano", coincidencias)
    assert rescate is not None
    assert rescate["nombre_escogido"] == "Banano*"


def test_no_rescata_nombres_compuestos_aunque_la_similitud_sea_alta():
    # "papa pastusa" da 1.0 de similitud contra "Papa negra" Y "Papa criolla"
    # a la vez (la palabra compartida "papa" domina el puntaje) — decidir
    # solo por similitud aquí sería incorrecto, así que el rescate se
    # abstiene y deja la decisión al modelo (o a la desambiguación previa).
    coincidencias = _mejores_coincidencias("papa pastusa", PRODUCTOS)
    assert _intentar_rescate_por_similitud("papa pastusa", coincidencias) is None


def test_no_rescata_typo_de_nombre_compuesto_como_manzana():
    # "mansana" (typo de "manzana") NO se rescata automáticamente porque en
    # los datos reales el nombre siempre viene con calificativo ("Manzana
    # royal gala", 3 palabras) — ese caso queda para el modelo, ya reforzado
    # con la sugerencia automática en el prompt (ver _bloque_sugerencias).
    coincidencias = _mejores_coincidencias("mansana", PRODUCTOS)
    assert coincidencias[0][0]["nombre"] == "Manzana royal gala"  # sí aparece sugerido...
    assert _intentar_rescate_por_similitud("mansana", coincidencias) is None  # ...pero no se auto-rescata


def test_no_rescata_si_la_distancia_de_edicion_es_grande():
    # Similitud alta (0.85) pero un cambio de palabra demasiado grande para
    # ser un typo razonable (más de 2 ediciones) -> no se rescata.
    coincidencias = [({"x": 1, "nombre": "Coco"}, 0.85)]
    assert _intentar_rescate_por_similitud("aeiou", coincidencias) is None


def test_no_rescata_si_no_hay_coincidencias():
    assert _intentar_rescate_por_similitud("cualquier cosa", []) is None


# ---------- Última red: "¿quisiste decir...?" (ver suggestions.py para el flujo completo) ----------


def test_variante_razonable_reconoce_typo_de_una_palabra():
    assert _es_variante_razonable("vanano", "Banano*") is True
    assert _es_variante_razonable("agucate", "Aguacate*") is True


def test_variante_razonable_permite_nombres_compuestos():
    # A diferencia del rescate automático, aquí SÍ se permiten nombres
    # compuestos porque el usuario tiene que confirmar.
    assert _es_variante_razonable("lechosa", "Lechuga Batavia") is True


def test_variante_no_razonable_por_coincidencia_de_letras():
    # El mismo caso que causaba el falso rescate de "vanano" con "vaina" —
    # aquí tampoco debe colarse como sugerencia válida.
    assert _es_variante_razonable("vanano", "Arveja verde en vaina") is False


def test_variante_no_razonable_para_productos_sin_relacion():
    assert _es_variante_razonable("uchuva", "Mandarina*") is False
    assert _es_variante_razonable("fresa", "Tomate*") is False


def test_construir_sugerencias_incluye_solo_lo_razonable():
    coincidencias = _mejores_coincidencias("vanano", PRODUCTOS)
    sugerencias = _construir_sugerencias("vanano", coincidencias)
    nombres = [s["nombre"] for s in sugerencias]
    assert "Banano*" in nombres
    assert "Arveja verde en vaina" not in nombres


def test_construir_sugerencias_vacio_si_nada_es_razonable():
    coincidencias = _mejores_coincidencias("uchuva", PRODUCTOS)
    assert _construir_sugerencias("uchuva", coincidencias) == []


def test_resultado_no_encontrado_trae_sugerencias_cuando_aplica():
    coincidencias = _mejores_coincidencias("vanano", PRODUCTOS)
    resultado = _resultado_no_encontrado("vanano", coincidencias)
    assert resultado["encontrado"] is False
    assert any(s["nombre"] == "Banano*" for s in resultado["sugerencias"])


def test_resultado_no_encontrado_sin_sugerencias_cuando_no_aplica():
    coincidencias = _mejores_coincidencias("uchuva", PRODUCTOS)
    resultado = _resultado_no_encontrado("uchuva", coincidencias)
    assert resultado["encontrado"] is False
    assert resultado["sugerencias"] == []
