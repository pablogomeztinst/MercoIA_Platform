from pathlib import Path

import pytest

from app.services.pricing import build_pricing_response, precio_disponible
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_parser import FilaSipsa, parse_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "sipsa_sample.xlsx"


@pytest.fixture(scope="module")
def boletin():
    return parse_workbook(FIXTURE.read_bytes())


def _candidato(**overrides) -> CandidatoFecha:
    base = dict(
        intento=1,
        fecha_dane="11ago2026",
        fecha_legible="11/08/2026",
        fecha_legible_larga="Martes 11 de agosto de 2026",
        fecha_objetivo_iso="2026-08-11",
        dia_semana="martes",
        url_sipsa="https://www.dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-11ago2026.xlsx",
        modo_busqueda_fecha="mas_reciente",
        fecha_solicitada=False,
        tipo_fecha_solicitada=None,
        descripcion_fecha_solicitada=None,
        offset_dias_solicitado=0,
        es_fecha_hoy=True,
    )
    base.update(overrides)
    return CandidatoFecha(**base)


def _index_de(boletin, nombre: str) -> int:
    for f in boletin.filas:
        if f.nombre == nombre:
            return f.index
    raise AssertionError(f"No se encontró {nombre!r} en el fixture")


def test_producto_encontrado_incluye_ambos_mercados(boletin):
    x = _index_de(boletin, "Papa negra*")
    normalizado = {"encontrado": True, "x": x, "nombre_escogido": "Papa negra", "confianza": 0.95}

    resultado = build_pricing_response(normalizado, boletin.filas, _candidato())

    assert resultado.encontrado is True
    # El nombre va en minúscula dentro de la frase ("de la papa negra"),
    # igual que en el flujo n8n original (nombreParaFrase.toLowerCase()).
    assert "papa negra" in resultado.respuesta_base.lower()
    assert "Centroabastos S.A." in resultado.respuesta_base
    assert "Corabastos S.A." in resultado.respuesta_base
    assert "COP por kilogramo" in resultado.respuesta_base
    assert "2.000 COP" in resultado.respuesta_base  # precio_centroabastos formateado es-CO
    assert "2.338 COP" in resultado.respuesta_base  # precio_corabastos formateado es-CO


def test_producto_no_encontrado():
    normalizado = {"encontrado": False, "x": -1, "nombre_escogido": None, "confianza": 0}
    resultado = build_pricing_response(normalizado, [], _candidato())
    assert resultado.encontrado is False
    assert "no encontré" in resultado.respuesta_base.lower()


def test_x_fuera_de_rango_no_revienta(boletin):
    normalizado = {"encontrado": True, "x": 9999, "nombre_escogido": "algo", "confianza": 0.5}
    resultado = build_pricing_response(normalizado, boletin.filas, _candidato())
    assert resultado.encontrado is False


def test_precio_no_disponible_se_muestra_como_no_disponible(boletin):
    x = _index_de(boletin, "Plátano guineo")  # n.d. en Centroabastos según el fixture
    normalizado = {"encontrado": True, "x": x, "nombre_escogido": "Plátano guineo", "confianza": 0.9}

    resultado = build_pricing_response(normalizado, boletin.filas, _candidato())

    assert "no hay información disponible" in resultado.respuesta_base


def test_precio_disponible_helper():
    assert precio_disponible(2500) is True
    assert precio_disponible("n.d.") is False
    assert precio_disponible(None) is False
    assert precio_disponible("") is False


def test_respaldo_de_mercado_se_usa_y_se_anota_la_fecha(boletin):
    # "Plátano guineo" en el fixture no trae precio de Centroabastos (n.d.),
    # simulando el caso real: ese mercado no vino ese día, pero
    # sipsa_fetch.buscar_precio_respaldo() sí encontró un precio real de un
    # día anterior para ese mismo producto.
    x = _index_de(boletin, "Plátano guineo")
    normalizado = {"encontrado": True, "x": x, "nombre_escogido": "Plátano guineo", "confianza": 0.9}

    resultado = build_pricing_response(
        normalizado,
        boletin.filas,
        _candidato(),
        respaldo_centroabastos=(1990, 0.02, "Martes 6 de agosto de 2026"),
    )

    assert resultado.encontrado is True
    assert "1.990 COP" in resultado.respuesta_base
    assert "dato más reciente disponible de este mercado: Martes 6 de agosto de 2026" in resultado.respuesta_base


def test_respaldo_no_se_usa_si_el_dato_original_ya_existe(boletin):
    # Si el boletín de referencia SÍ trae el precio, el respaldo se ignora
    # (nunca debe pisar un dato real y más actual con uno más viejo).
    x = _index_de(boletin, "Papa negra*")
    normalizado = {"encontrado": True, "x": x, "nombre_escogido": "Papa negra", "confianza": 0.95}

    resultado = build_pricing_response(
        normalizado,
        boletin.filas,
        _candidato(),
        respaldo_centroabastos=(1, 0, "Lunes 1 de enero de 2020"),
    )

    assert "Lunes 1 de enero de 2020" not in resultado.respuesta_base
    assert "2.000 COP" in resultado.respuesta_base  # precio real del fixture, no el respaldo


# ---------- nota_fecha: fila "completada" desde otra fuente/fecha (ver sipsa_fetch._completar_con_fuente_principal) ----------


def test_nota_fecha_se_muestra_en_ambos_mercados_si_la_fila_es_de_otra_fecha():
    # Ej. "Fresa" no estaba en el respaldo (Excel) del día, pero sí en el
    # catálogo de la fuente principal (Pentaho) de un día anterior -- la fila
    # "importada" trae nota_fecha para que quede claro que ese precio no es
    # del día del informe.
    fila = FilaSipsa(
        index=1,
        nombre="Fresa",
        precio_centroabastos=13000,
        var_centroabastos=0.01,
        precio_corabastos=14375,
        var_corabastos=0.07,
        nota_fecha="Lunes 7 de septiembre de 2026",
    )
    normalizado = {"encontrado": True, "x": 1, "nombre_escogido": "Fresa", "confianza": 0.9}

    resultado = build_pricing_response(normalizado, [FilaSipsa(index=0, nombre="Miércoles 9 de septiembre de 2026"), fila], _candidato())

    assert resultado.encontrado is True
    assert resultado.respuesta_base.count("Lunes 7 de septiembre de 2026") == 2  # una vez por mercado


def test_nota_fecha_no_se_muestra_si_el_mercado_no_tiene_precio():
    # Si ese mercado puntual no tiene precio ni siquiera en la fila
    # "completada", no tiene sentido anotar una fecha para un dato que no se
    # está mostrando.
    fila = FilaSipsa(
        index=1,
        nombre="Fresa",
        precio_centroabastos=None,
        var_centroabastos=None,
        precio_corabastos=14375,
        var_corabastos=0.07,
        nota_fecha="Lunes 7 de septiembre de 2026",
    )
    normalizado = {"encontrado": True, "x": 1, "nombre_escogido": "Fresa", "confianza": 0.9}

    resultado = build_pricing_response(normalizado, [FilaSipsa(index=0, nombre="titulo"), fila], _candidato())

    assert resultado.respuesta_base.count("Lunes 7 de septiembre de 2026") == 1  # solo para Corabastos


def test_respaldo_de_mercado_tiene_prioridad_sobre_nota_fecha():
    # Si además hay un respaldo específico de mercado (búsqueda propia hacia
    # atrás para ESE mercado puntual), su fecha es más precisa que la
    # nota_fecha genérica de la fila completa -- debe ganar esa.
    fila = FilaSipsa(
        index=1,
        nombre="Fresa",
        precio_centroabastos=None,
        var_centroabastos=None,
        precio_corabastos=14375,
        var_corabastos=0.07,
        nota_fecha="Lunes 7 de septiembre de 2026",
    )
    normalizado = {"encontrado": True, "x": 1, "nombre_escogido": "Fresa", "confianza": 0.9}

    resultado = build_pricing_response(
        normalizado,
        [FilaSipsa(index=0, nombre="titulo"), fila],
        _candidato(),
        respaldo_centroabastos=(12000, 0.0, "Martes 8 de septiembre de 2026"),
    )

    assert "Martes 8 de septiembre de 2026" in resultado.respuesta_base
    assert "Lunes 7 de septiembre de 2026" in resultado.respuesta_base  # sigue apareciendo para Corabastos
