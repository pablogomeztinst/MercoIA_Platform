"""Tests del menú inicial (ver menu.py). No se testea es_primer_contacto()
porque depende de una sesión real de PostgreSQL; el resto de la lógica
(elección pendiente en Redis) es determinística y se cubre aquí."""
import pytest
from fakeredis import FakeAsyncRedis

from app.services import menu


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


@pytest.mark.asyncio
async def test_sin_menu_pendiente_no_aplica(redis):
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "papa")
    assert resolucion.tipo == "no_aplica"
    assert resolucion.mensaje_usuario == "papa"


@pytest.mark.asyncio
async def test_elige_opcion_precios_por_numero(redis):
    await menu.marcar_menu_pendiente(redis, "573000000000")
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "1")
    assert resolucion.tipo == "precios_confirmado"


@pytest.mark.asyncio
async def test_elige_opcion_precios_por_palabra(redis):
    await menu.marcar_menu_pendiente(redis, "573000000000")
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "Precios")
    assert resolucion.tipo == "precios_confirmado"


@pytest.mark.asyncio
async def test_elige_opcion_enfermedades_por_numero(redis):
    await menu.marcar_menu_pendiente(redis, "573000000000")
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "2")
    assert resolucion.tipo == "enfermedades"


@pytest.mark.asyncio
async def test_elige_opcion_enfermedades_por_palabra(redis):
    await menu.marcar_menu_pendiente(redis, "573000000000")
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "enfermedades")
    assert resolucion.tipo == "enfermedades"


@pytest.mark.asyncio
async def test_ignora_el_menu_y_escribe_el_producto_de_una_vez(redis):
    # No respondió "1"/"2": escribió directo el nombre del producto. Se
    # asume el módulo de precios implícitamente y se deja seguir el mensaje
    # tal cual, en vez de insistir con el menú.
    await menu.marcar_menu_pendiente(redis, "573000000000")
    resolucion = await menu.resolver_pendiente(redis, "573000000000", "papa criolla")
    assert resolucion.tipo == "precios_implicito"
    assert resolucion.mensaje_usuario == "papa criolla"


@pytest.mark.asyncio
async def test_menu_pendiente_se_consume_una_sola_vez(redis):
    await menu.marcar_menu_pendiente(redis, "573000000000")
    await menu.resolver_pendiente(redis, "573000000000", "1")

    resolucion2 = await menu.resolver_pendiente(redis, "573000000000", "papa")
    assert resolucion2.tipo == "no_aplica"


def test_mensajes_mencionan_ambas_opciones():
    assert "1" in menu.MENSAJE_MENU_INICIAL and "2" in menu.MENSAJE_MENU_INICIAL
    assert "precios" in menu.MENSAJE_MENU_INICIAL.lower()
    assert "enfermedades" in menu.MENSAJE_MENU_INICIAL.lower()
    assert "desarrollo" in menu.MENSAJE_ENFERMEDADES_NO_DISPONIBLE.lower()
