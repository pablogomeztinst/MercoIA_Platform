"""Tests de sipsa_pentaho.py: la parte determinística (cálculo de precio +
variación a partir de la serie de precios por fecha) y el cortacircuitos
ante caídas del servicio (ver docstring del módulo -- agregado tras un
incidente real en producción, 17-sep-2026, donde una caída de Pentaho causó
decenas de llamadas de red fallidas por un solo mensaje de usuario, lo
bastante lentas como para que WhatsApp/el usuario reintentaran el mensaje).
No se testea contra la API real del DANE (igual que sipsa_fetch._descargar
no se testea directamente); el cortacircuitos se prueba con un http_client
falso que siempre falla."""
import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.services import sipsa_pentaho
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_pentaho import _calcular_variacion


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


@pytest.fixture(autouse=True)
def _sin_pausa_de_reintento(monkeypatch):
    # El reintento real espera 1s (ver sipsa_pentaho._doquery); en los tests
    # no hace falta esperar de verdad.
    monkeypatch.setattr(sipsa_pentaho.settings, "sipsa_pentaho_reintento_espera_segundos", 0)


class _HttpClienteQueFalla:
    """Simula que Pentaho no responde: cada .post() cuenta una llamada real
    de red y lanza un error de conexión."""

    def __init__(self):
        self.llamadas = 0

    async def post(self, *a, **kw):
        self.llamadas += 1
        raise httpx.ConnectError("simulado: Pentaho no responde")


def _candidato(fecha_iso: str = "2026-09-17") -> CandidatoFecha:
    return CandidatoFecha(
        intento=1, fecha_dane="17sep2026", fecha_legible="17/09/2026",
        fecha_legible_larga="Jueves 17 de septiembre de 2026", fecha_objetivo_iso=fecha_iso,
        dia_semana="jueves", url_sipsa="https://example.com/x.xlsx",
        modo_busqueda_fecha="mas_reciente", fecha_solicitada=False, tipo_fecha_solicitada=None,
        descripcion_fecha_solicitada=None, offset_dias_solicitado=0, es_fecha_hoy=True,
    )


def test_sin_precio_para_la_fecha_objetivo():
    precio, var = _calcular_variacion({"2026-09-01": 2000.0}, "2026-09-03")
    assert precio is None
    assert var is None


def test_sin_historial_anterior_no_hay_variacion():
    precio, var = _calcular_variacion({"2026-09-03": 2000.0}, "2026-09-03")
    assert precio == 2000.0
    assert var is None


def test_variacion_positiva_como_fraccion():
    # 2000 -> 2200 es +10%, se espera 0.10 (fracción, no porcentaje) porque
    # pricing.py ya multiplica por 100 cuando el valor viene en esa forma.
    precios = {"2026-09-01": 2000.0, "2026-09-03": 2200.0}
    precio, var = _calcular_variacion(precios, "2026-09-03")
    assert precio == 2200.0
    assert round(var, 4) == 0.10


def test_variacion_negativa():
    precios = {"2026-09-01": 2000.0, "2026-09-03": 1800.0}
    precio, var = _calcular_variacion(precios, "2026-09-03")
    assert precio == 1800.0
    assert round(var, 4) == -0.10


def test_usa_el_dia_anterior_mas_reciente_no_el_mas_viejo():
    # Con huecos (fin de semana, festivo, mercado no reportó ese día
    # puntual): debe comparar contra el más reciente disponible ANTES del
    # objetivo, no contra el primero de la serie.
    precios = {"2026-08-20": 1000.0, "2026-09-01": 1900.0, "2026-09-03": 2000.0}
    precio, var = _calcular_variacion(precios, "2026-09-03")
    assert precio == 2000.0
    assert round(var, 4) == round((2000.0 - 1900.0) / 1900.0, 4)


def test_precio_anterior_cero_no_produce_division_por_cero():
    precios = {"2026-09-01": 0.0, "2026-09-03": 2000.0}
    precio, var = _calcular_variacion(precios, "2026-09-03")
    assert precio == 2000.0
    assert var is None


# ---------- Cortacircuitos ante caídas de Pentaho ----------


@pytest.mark.asyncio
async def test_una_falla_persistente_abre_el_circuito(redis):
    # 2 llamadas: el intento inicial + 1 reintento corto (ver _doquery) antes
    # de rendirse -- una falla aislada ya NO abre el circuito de una, para no
    # bloquear de paso un intento legítimo y distinto que necesite pasar
    # justo después dentro del mismo mensaje (ver el siguiente test).
    assert await sipsa_pentaho._circuito_abierto(redis) is False

    http = _HttpClienteQueFalla()
    resultado = await sipsa_pentaho.obtener_boletin_pentaho(_candidato(), redis=redis, http_client=http)

    assert resultado is None
    assert http.llamadas == 2
    assert await sipsa_pentaho._circuito_abierto(redis) is True


@pytest.mark.asyncio
async def test_un_tropiezo_aislado_no_bloquea_un_intento_distinto_inmediatamente_despues(redis):
    # Este es el incidente real del 17-sep-2026: la fuente principal falló
    # una vez al resolver el boletín de hoy (tropiezo aislado, se recupera en
    # el reintento), y ESO no debía impedir que, milisegundos después, un
    # intento legítimo y distinto (ej. completar productos faltantes desde
    # Pentaho, ver sipsa_fetch._completar_con_fuente_principal) también
    # tuviera su propia oportunidad real. El http_client falla UNA sola vez
    # y luego responde bien -- con el reintento corto, ni siquiera debería
    # notarse afuera.
    class _FallaUnaVezLuegoResponde:
        def __init__(self):
            self.llamadas = 0

        async def post(self, *a, **kw):
            self.llamadas += 1
            if self.llamadas == 1:
                raise httpx.ConnectError("simulado: un solo tropiezo")

            class _Resp:
                status_code = 200

                def json(self_inner):
                    return {"resultset": [["Fresa"]]}

            return _Resp()

    http = _FallaUnaVezLuegoResponde()
    articulos = await sipsa_pentaho._listar_articulos(http, redis)

    assert articulos == ["Fresa"]
    assert await sipsa_pentaho._circuito_abierto(redis) is False


@pytest.mark.asyncio
async def test_con_el_circuito_abierto_no_se_repite_ninguna_llamada_de_red(redis):
    # Escenario del incidente original: un solo mensaje de usuario que
    # dispara muchos intentos (varios días candidatos, o los 10 productos del
    # resumen) no debe traducirse en muchas llamadas de red fallidas, una vez
    # que ya se confirmó (con su reintento) que Pentaho está caído.
    http = _HttpClienteQueFalla()

    await sipsa_pentaho.obtener_boletin_pentaho(_candidato("2026-09-17"), redis=redis, http_client=http)
    assert http.llamadas == 2

    for fecha in ("2026-09-16", "2026-09-15", "2026-09-14", "2026-09-13"):
        resultado = await sipsa_pentaho.obtener_boletin_pentaho(_candidato(fecha), redis=redis, http_client=http)
        assert resultado is None

    # Ninguno de esos 4 intentos adicionales debió tocar la red: el
    # cortacircuitos seguía abierto.
    assert http.llamadas == 2


@pytest.mark.asyncio
async def test_el_circuito_se_cierra_al_vencer_el_ttl(redis):
    http = _HttpClienteQueFalla()

    await sipsa_pentaho.obtener_boletin_pentaho(_candidato(), redis=redis, http_client=http)
    assert http.llamadas == 2

    # Simula que el TTL ya venció (en vez de esperar el tiempo real): al
    # desaparecer la marca, el siguiente intento sí debe volver a la red.
    await redis.delete(sipsa_pentaho._REDIS_KEY_CIRCUITO)
    await sipsa_pentaho.obtener_boletin_pentaho(_candidato(), redis=redis, http_client=http)
    assert http.llamadas == 4
