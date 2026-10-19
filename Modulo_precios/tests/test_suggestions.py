import pytest
from fakeredis import FakeAsyncRedis

from app.services import suggestions


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


SUGERENCIAS = [
    {"x": 8, "nombre": "Banano*"},
    {"x": 30, "nombre": "Mango Tommy"},
]


def test_construir_mensaje_lista_las_sugerencias_reales():
    mensaje = suggestions.construir_mensaje_sugerencias(SUGERENCIAS)
    assert "Banano" in mensaje
    assert "Mango Tommy" in mensaje
    assert "*" not in mensaje  # limpiar_nombre_producto quita el asterisco


@pytest.mark.asyncio
async def test_resolver_pendiente_confirma_una_sugerencia(redis):
    await suggestions.guardar_contexto(redis, "573000000000", "vanano", SUGERENCIAS)

    resolucion = await suggestions.resolver_pendiente(redis, "573000000000", "Banano")

    assert resolucion.resuelto is True
    assert resolucion.mensaje_usuario == "Banano"


@pytest.mark.asyncio
async def test_resolver_pendiente_tolera_typo_en_la_confirmacion(redis):
    await suggestions.guardar_contexto(redis, "573000000000", "vanano", SUGERENCIAS)

    resolucion = await suggestions.resolver_pendiente(redis, "573000000000", "banao")

    assert resolucion.resuelto is True
    assert "Banano" in resolucion.mensaje_usuario


@pytest.mark.asyncio
async def test_resolver_pendiente_sin_coincidencia_limpia_contexto(redis):
    await suggestions.guardar_contexto(redis, "573000000000", "vanano", SUGERENCIAS)

    resolucion = await suggestions.resolver_pendiente(redis, "573000000000", "zanahoria")
    assert resolucion.resuelto is False
    assert resolucion.mensaje_usuario == "zanahoria"

    # el contexto ya se borró: una confirmación tardía ya no aplica
    resolucion2 = await suggestions.resolver_pendiente(redis, "573000000000", "Banano")
    assert resolucion2.resuelto is False


@pytest.mark.asyncio
async def test_resolver_pendiente_sin_contexto_previo(redis):
    resolucion = await suggestions.resolver_pendiente(redis, "573000000000", "papa negra")
    assert resolucion.resuelto is False
    assert resolucion.mensaje_usuario == "papa negra"


@pytest.mark.asyncio
async def test_olvidar_pendiente_borra_el_contexto(redis):
    await suggestions.guardar_contexto(redis, "573000000000", "vanano", SUGERENCIAS)

    await suggestions.olvidar_pendiente(redis, "573000000000")

    resolucion = await suggestions.resolver_pendiente(redis, "573000000000", "Banano")
    assert resolucion.resuelto is False
