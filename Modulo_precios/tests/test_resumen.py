import json
from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis

from app.services import sipsa_fetch
from app.services.resumen import construir_resumen
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_parser import FilaSipsa, parse_workbook

FIXTURE = Path(__file__).parent / "fixtures" / "sipsa_sample.xlsx"


class _DBFalsa:
    """No debería llegar a usarse en estos tests: cuando hace falta un
    respaldo, el boletín anterior ya está precargado en Redis (ver
    test_usa_bucaramanga_si_es_mas_reciente_que_bogota)."""

    async def execute(self, *a, **kw):
        raise AssertionError("no se esperaba tocar Postgres en este test")

    async def commit(self):
        pass


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


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


@pytest.mark.asyncio
async def test_resumen_con_boletin_real_incluye_los_disponibles_y_marca_los_faltantes(redis):
    boletin = parse_workbook(FIXTURE.read_bytes())
    # fecha_especifica: no busca respaldo hacia atrás, usa el boletín tal cual.
    candidato = _candidato(modo_busqueda_fecha="fecha_especifica", fecha_solicitada=True)

    respuesta = await construir_resumen(
        boletin.filas, candidato, [candidato], redis=redis, db=_DBFalsa(), http_client=None
    )

    # El fixture sí trae estos 5 de los 10 (con su nombre exacto), ambos
    # mercados presentes ese mismo día -> se anota el mercado, sin fecha.
    assert "Zanahoria: *1.875 COP/kg* _(Bogotá)_" in respuesta
    assert "Limón Tahití: *2.018 COP/kg* _(Bogotá)_" in respuesta
    assert "Plátano hartón verde: *2.125 COP/kg* _(Bogotá)_" in respuesta
    assert "Cebolla cabezona blanca: *2.825 COP/kg* _(Bogotá)_" in respuesta
    assert "Cebolla junca: *2.458 COP/kg* _(Bogotá)_" in respuesta

    # El fixture NO trae estas variedades exactas -> no disponible.
    assert "Papa parda pastusa: _no disponible_" in respuesta
    assert "Yuca criolla: _no disponible_" in respuesta
    assert "Tomate chonto: _no disponible_" in respuesta
    assert "Aguacate Hass: _no disponible_" in respuesta
    assert "Banano criollo: _no disponible_" in respuesta

    assert "¿Deseas consultar otro precio?" in respuesta


@pytest.mark.asyncio
async def test_usa_bucaramanga_si_bogota_no_reporto_ese_dia(redis):
    filas = [
        FilaSipsa(index=0, nombre="titulo"),
        FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos="n.d.", precio_centroabastos=1500),
    ]
    candidato = _candidato(modo_busqueda_fecha="fecha_especifica", fecha_solicitada=True)

    respuesta = await construir_resumen(filas, candidato, [candidato], redis=redis, db=_DBFalsa(), http_client=None)

    assert "Zanahoria: *1.500 COP/kg* _(Bucaramanga)_" in respuesta


@pytest.mark.asyncio
async def test_sin_precio_en_ningun_mercado_y_sin_respaldo_permitido_es_no_disponible(redis):
    filas = [
        FilaSipsa(index=0, nombre="titulo"),
        FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=None, precio_centroabastos="n.d."),
    ]
    # fecha_especifica: no se busca hacia atrás aunque falte el precio.
    candidato = _candidato(modo_busqueda_fecha="fecha_especifica", fecha_solicitada=True)

    respuesta = await construir_resumen(filas, candidato, [candidato], redis=redis, db=_DBFalsa(), http_client=None)

    assert "Zanahoria: _no disponible_" in respuesta


@pytest.mark.asyncio
async def test_usa_el_mercado_mas_reciente_entre_ambos_no_siempre_bogota(redis):
    # Bogotá no reportó Zanahoria hoy (11-ago); Bucaramanga tampoco. Pero
    # Bucaramanga SÍ tiene un precio de ayer (10-ago) en el boletín cacheado,
    # que es lo único disponible -> debe mostrarse eso, marcado como
    # Bucaramanga y con la fecha real (10-ago), en vez de "no disponible".
    filas_hoy = [
        FilaSipsa(index=0, nombre="Martes 11 de agosto de 2026"),
        FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos="n.d.", precio_centroabastos="n.d."),
    ]
    filas_ayer = [
        FilaSipsa(index=0, nombre="Lunes 10 de agosto de 2026"),
        FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos="n.d.", precio_centroabastos=1400),
    ]
    await redis.set(
        sipsa_fetch._redis_key("2026-08-10"),
        json.dumps(sipsa_fetch._boletin_to_dict(sipsa_fetch.BoletinSipsa(fecha_reporte_texto="Lunes 10 de agosto de 2026", filas=filas_ayer))),
        ex=3600,
    )

    candidato_hoy = _candidato(offset_dias_solicitado=0)
    candidato_ayer = _candidato(
        fecha_dane="10ago2026", fecha_legible="10/08/2026", fecha_legible_larga="Lunes 10 de agosto de 2026",
        fecha_objetivo_iso="2026-08-10", offset_dias_solicitado=1, es_fecha_hoy=False,
    )

    respuesta = await construir_resumen(
        filas_hoy, candidato_hoy, [candidato_hoy, candidato_ayer], redis=redis, db=_DBFalsa(), http_client=None
    )

    assert "Zanahoria: *1.400 COP/kg* _(Bucaramanga, Lunes 10 de agosto de 2026)_" in respuesta


@pytest.mark.asyncio
async def test_incluye_los_10_productos_siempre_en_el_mismo_orden(redis):
    candidato = _candidato(modo_busqueda_fecha="fecha_especifica", fecha_solicitada=True)
    respuesta = await construir_resumen(
        [FilaSipsa(index=0, nombre="titulo")], candidato, [candidato], redis=redis, db=_DBFalsa(), http_client=None
    )
    nombres = [
        "Papa parda pastusa", "Plátano hartón verde", "Yuca criolla",
        "Cebolla cabezona blanca", "Cebolla junca", "Tomate chonto",
        "Zanahoria", "Aguacate Hass", "Limón Tahití", "Banano criollo",
    ]
    posiciones = [respuesta.index(n) for n in nombres]
    assert posiciones == sorted(posiciones)
