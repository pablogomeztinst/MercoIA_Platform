from pathlib import Path

import pytest

from app.services.sipsa_parser import construir_lista_productos, parse_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "sipsa_sample.xlsx"
FIXTURE_SIN_BUCARAMANGA = Path(__file__).parent / "fixtures" / "sipsa_sample_sin_bucaramanga.xlsx"


@pytest.fixture(scope="module")
def boletin():
    return parse_workbook(FIXTURE.read_bytes())


def test_fecha_reporte(boletin):
    assert boletin.fecha_reporte_texto == "Martes 11 de agosto de 2026"


def test_encuentra_papa_negra_y_criolla(boletin):
    nombres = {f.nombre: f for f in boletin.filas if f.nombre}
    assert "Papa negra*" in nombres
    assert "Papa  criolla" in nombres

    papa_negra = nombres["Papa negra*"]
    assert papa_negra.precio_corabastos == 2338
    assert papa_negra.precio_centroabastos == 2000


def test_encuentra_platano_guineo_y_harton(boletin):
    nombres = {f.nombre: f for f in boletin.filas if f.nombre}
    assert "Plátano guineo" in nombres
    assert "Plátano hartón verde" in nombres


def test_construir_lista_productos_excluye_encabezados(boletin):
    productos = construir_lista_productos(boletin.filas)
    nombres = {p["nombre"] for p in productos}
    assert "Verduras y hortalizas" not in nombres
    assert "Frutas frescas" not in nombres
    assert "Ahuyama" in nombres
    assert len(productos) > 20


def test_x_de_cada_producto_permite_recuperar_la_fila(boletin):
    productos = construir_lista_productos(boletin.filas)
    for p in productos[:5]:
        assert boletin.filas[p["x"]].nombre == p["nombre"]


# ---------- Boletín real que NO reporta Bucaramanga ese día (12 de agosto) ----------
# Regresión: antes, si faltaba Bucaramanga-Centroabastos, todo el boletín se
# rechazaba (SipsaParseError) aunque Bogotá-Corabastos sí viniera completo,
# y la búsqueda saltaba innecesariamente más días atrás de lo debido.


@pytest.fixture(scope="module")
def boletin_sin_bucaramanga():
    return parse_workbook(FIXTURE_SIN_BUCARAMANGA.read_bytes())


def test_parsea_sin_reventar_aunque_falte_bucaramanga(boletin_sin_bucaramanga):
    assert boletin_sin_bucaramanga.fecha_reporte_texto == "Miércoles 12 de agosto de 2026"
    assert len(boletin_sin_bucaramanga.filas) > 20


def test_bogota_si_tiene_datos_aunque_falte_bucaramanga(boletin_sin_bucaramanga):
    nombres = {f.nombre: f for f in boletin_sin_bucaramanga.filas if f.nombre}
    guayaba = nombres["Guayaba*"]
    assert guayaba.precio_corabastos is not None
    assert isinstance(guayaba.precio_corabastos, (int, float))


def test_centroabastos_queda_none_cuando_no_se_reporto(boletin_sin_bucaramanga):
    nombres = {f.nombre: f for f in boletin_sin_bucaramanga.filas if f.nombre}
    guayaba = nombres["Guayaba*"]
    assert guayaba.precio_centroabastos is None
    assert guayaba.var_centroabastos is None


def test_construir_lista_productos_funciona_igual_sin_bucaramanga(boletin_sin_bucaramanga):
    # es_producto() acepta la fila si tiene precio en CUALQUIERA de los dos
    # mercados, así que Bogotá solo basta para que el producto sea válido.
    productos = construir_lista_productos(boletin_sin_bucaramanga.filas)
    nombres = {p["nombre"] for p in productos}
    assert "Guayaba*" in nombres
    assert len(productos) > 20
