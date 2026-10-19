from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis

from app.services import ambiguous
from app.services.sipsa_parser import construir_lista_productos, parse_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "sipsa_sample.xlsx"


@pytest.fixture(scope="module")
def productos():
    boletin = parse_workbook(FIXTURE.read_bytes())
    return construir_lista_productos(boletin.filas)


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


# ---------- Agrupación dinámica contra el boletín real ----------


def test_agrupar_por_base_encuentra_los_4_grupos_reales(productos):
    grupos = {g.base: sorted(o.distintivo for o in g.opciones) for g in ambiguous.agrupar_por_base(productos)}
    assert grupos["papa"] == sorted(["negra", "criolla"])
    assert grupos["platano"] == sorted(["guineo", "harton verde"])
    assert grupos["limon"] == sorted(["comun", "tahiti"])
    assert grupos["cebolla"] == sorted(["cabezona blanca", "junca"])


def test_aguacate_no_es_grupo_ambiguo(productos):
    # El boletín solo trae "Aguacate*" (variedad predominante) - una sola fila.
    grupos = {g.base for g in ambiguous.agrupar_por_base(productos)}
    assert "aguacate" not in grupos


def test_tomate_no_es_ambiguo_por_tener_nombre_pelado(productos):
    # "Tomate*" (pelado) y "Tomate de árbol" comparten la palabra base, pero
    # como una de las dos es el nombre sin calificativo, se asume que ES la
    # opción por defecto y no hace falta preguntar.
    grupos = {g.base for g in ambiguous.agrupar_por_base(productos)}
    assert "tomate" not in grupos


# ---------- Detección de ambigüedad en un mensaje ----------


def test_detectar_ambiguedad_papa_sin_variedad(productos):
    grupo = ambiguous.detectar_ambiguedad("precio de la papa", productos)
    assert grupo is not None
    assert grupo.base == "papa"


def test_detectar_ambiguedad_papa_con_variedad_no_es_ambiguo(productos):
    assert ambiguous.detectar_ambiguedad("precio de la papa criolla", productos) is None
    assert ambiguous.detectar_ambiguedad("papa negra", productos) is None


def test_detectar_ambiguedad_limon(productos):
    grupo = ambiguous.detectar_ambiguedad("cuanto vale el limon", productos)
    assert grupo is not None
    assert grupo.base == "limon"


def test_detectar_ambiguedad_limon_tahiti_tolera_typos(productos):
    assert ambiguous.detectar_ambiguedad("limon tahiti", productos) is None
    assert ambiguous.detectar_ambiguedad("limon taiti", productos) is None


def test_detectar_ambiguedad_cebolla(productos):
    grupo = ambiguous.detectar_ambiguedad("cebolla", productos)
    assert grupo is not None
    assert grupo.base == "cebolla"


def test_detectar_ambiguedad_platano(productos):
    grupo = ambiguous.detectar_ambiguedad("precio del platano hoy", productos)
    assert grupo is not None
    assert grupo.base == "platano"


def test_producto_sin_grupo_no_es_ambiguo(productos):
    assert ambiguous.detectar_ambiguedad("tomate", productos) is None
    assert ambiguous.detectar_ambiguedad("zanahoria", productos) is None


# ---------- Mensaje de aclaración ----------


def test_construir_respuesta_ambigua_lista_las_opciones_reales(productos):
    grupo = ambiguous.detectar_ambiguedad("cebolla", productos)
    respuesta = ambiguous.construir_respuesta_ambigua(grupo)
    assert "Cebolla cabezona blanca" in respuesta
    assert "Cebolla junca" in respuesta


# ---------- Resolución de contexto pendiente (multi-turno) ----------


@pytest.mark.asyncio
async def test_resolver_pendiente_completo(redis, productos):
    grupo = ambiguous.detectar_ambiguedad("precio de la papa anteayer", productos)
    await ambiguous.guardar_contexto(redis, "573000000000", grupo, "precio de la papa anteayer")

    resolucion = await ambiguous.resolver_pendiente(redis, "573000000000", "negra")

    assert resolucion.resuelto is True
    assert resolucion.mensaje_usuario == "precio de la papa anteayer Papa negra"


@pytest.mark.asyncio
async def test_resolver_pendiente_limon(redis, productos):
    grupo = ambiguous.detectar_ambiguedad("limon", productos)
    await ambiguous.guardar_contexto(redis, "573000000000", grupo, "limon")

    resolucion = await ambiguous.resolver_pendiente(redis, "573000000000", "tahiti")

    assert resolucion.resuelto is True
    assert "Tahití" in resolucion.mensaje_usuario


@pytest.mark.asyncio
async def test_resolver_pendiente_no_coincide_limpia_contexto(redis, productos):
    grupo = ambiguous.detectar_ambiguedad("papa", productos)
    await ambiguous.guardar_contexto(redis, "573000000000", grupo, "papa")

    resolucion = await ambiguous.resolver_pendiente(redis, "573000000000", "tomate")
    assert resolucion.resuelto is False
    assert resolucion.mensaje_usuario == "tomate"

    # el contexto viejo ya se borró: una respuesta tardía "negra" ya no aplica
    resolucion2 = await ambiguous.resolver_pendiente(redis, "573000000000", "negra")
    assert resolucion2.resuelto is False


@pytest.mark.asyncio
async def test_resolver_pendiente_sin_contexto_previo(redis):
    resolucion = await ambiguous.resolver_pendiente(redis, "573000000000", "papa negra")
    assert resolucion.resuelto is False
    assert resolucion.mensaje_usuario == "papa negra"


@pytest.mark.asyncio
async def test_olvidar_pendiente_borra_el_contexto(redis, productos):
    # Ej. el usuario pidió volver al menú a mitad de la desambiguación: una
    # respuesta posterior sin relación ("hola") ya no debe leerse como si
    # respondiera la pregunta vieja de papa negra/criolla.
    grupo = ambiguous.detectar_ambiguedad("papa", productos)
    await ambiguous.guardar_contexto(redis, "573000000000", grupo, "papa")

    await ambiguous.olvidar_pendiente(redis, "573000000000")

    resolucion = await ambiguous.resolver_pendiente(redis, "573000000000", "negra")
    assert resolucion.resuelto is False
    assert resolucion.mensaje_usuario == "negra"
