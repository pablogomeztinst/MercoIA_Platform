"""Tests de sipsa_fetch.py enfocados en la robustez ante caídas de Pentaho
(ver la conversación con el usuario, incidentes reales del 17-sep-2026):

- _completar_con_fuente_principal / _resolver_fuente_especifica deben poder
  completar productos usando un boletín de Pentaho YA GUARDADO de un día
  anterior (caché Redis o Postgres), sin depender de que Pentaho esté
  disponible EN VIVO en este momento -- antes de este fix, una caída total
  de Pentaho bloqueaba la completación por completo aunque tuviéramos
  productos como "Fresa" ya guardados de hace unos días.

No se testea obtener_boletin() de punta a punta contra la red real (igual
que en el resto del proyecto); aquí se usa FakeAsyncRedis pre-cargado y un
http_client que siempre falla, para simular una caída total de Pentaho.
"""
import json

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.services import sipsa_fetch
from app.services.sipsa_dates import CandidatoFecha
from app.services.sipsa_parser import BoletinSipsa, FilaSipsa


class _DBSinNada:
    """Postgres vacío: nunca hay nada guardado ahí en estos tests (todo lo
    relevante ya está en la caché Redis pre-cargada)."""

    async def execute(self, *a, **kw):
        class _Resultado:
            def scalar_one_or_none(self_inner):
                return None

        return _Resultado()

    async def commit(self):
        pass


class _HttpClienteSiempreCaido:
    """Simula una caída TOTAL de Pentaho: cualquier llamada falla."""

    async def post(self, *a, **kw):
        raise httpx.ConnectError("simulado: Pentaho completamente caído")


@pytest.fixture
async def redis():
    r = FakeAsyncRedis()
    yield r
    await r.aclose()


def _candidato(fecha_iso: str, offset: int) -> CandidatoFecha:
    return CandidatoFecha(
        intento=1, fecha_dane=f"{fecha_iso}", fecha_legible=fecha_iso,
        fecha_legible_larga=f"Día {fecha_iso}", fecha_objetivo_iso=fecha_iso,
        dia_semana="x", url_sipsa="https://example.com/x.xlsx",
        modo_busqueda_fecha="mas_reciente", fecha_solicitada=False, tipo_fecha_solicitada=None,
        descripcion_fecha_solicitada=None, offset_dias_solicitado=offset, es_fecha_hoy=(offset == 0),
    )


@pytest.mark.asyncio
async def test_completa_con_un_boletin_ya_guardado_aunque_pentaho_este_totalmente_caido(redis, monkeypatch):
    monkeypatch.setattr(sipsa_fetch.settings, "sipsa_pentaho_reintento_espera_segundos", 0)

    # Boletín de hace unos días, ya resuelto y cacheado antes (ej. por
    # Pentaho cuando sí funcionaba) -- incluye "Fresa" y más productos que el
    # respaldo de hoy (así se reconoce como genuinamente más completo, ver
    # `minimo_productos` en _resolver_fuente_especifica).
    filas_viejas = [
        FilaSipsa(index=0, nombre="Día 2026-09-10"),
        FilaSipsa(index=1, nombre="Fresa", precio_corabastos=14000, precio_centroabastos=13500),
        FilaSipsa(index=2, nombre="Zanahoria", precio_corabastos=1850),
        FilaSipsa(index=3, nombre="Mango", precio_corabastos=3200),
    ]
    await redis.set(
        sipsa_fetch._redis_key("2026-09-10"),
        json.dumps(sipsa_fetch._boletin_to_dict(BoletinSipsa(fecha_reporte_texto="Día 2026-09-10", filas=filas_viejas))),
        ex=3600,
    )

    # Boletín de hoy, vía respaldo (Excel), sin Fresa -- lo que dispara la
    # completación.
    boletin_hoy = BoletinSipsa(
        fecha_reporte_texto="Día 2026-09-17",
        filas=[FilaSipsa(index=0, nombre="Día 2026-09-17"), FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1900)],
    )
    candidato_hoy = _candidato("2026-09-17", 0)
    candidatos = [candidato_hoy, _candidato("2026-09-10", 7)]

    resultado = await sipsa_fetch._completar_con_fuente_principal(
        boletin_hoy, candidato_hoy, candidatos, redis=redis, db=_DBSinNada(), http_client=_HttpClienteSiempreCaido()
    )

    nombres = [f.nombre for f in resultado.filas]
    assert "Fresa" in nombres
    fila_fresa = next(f for f in resultado.filas if f.nombre == "Fresa")
    assert fila_fresa.precio_corabastos == 14000
    assert fila_fresa.nota_fecha == "Día 2026-09-10"  # se aclara que no es de hoy

    # Lo que ya traía el boletín de hoy no se pierde.
    assert "Zanahoria" in nombres


@pytest.mark.asyncio
async def test_no_se_confunde_con_su_propio_boletin_incompleto_ya_cacheado(redis, monkeypatch):
    # Bug real de producción (17-sep-2026): un turno posterior para el mismo
    # día ya había cacheado el boletín de HOY incompleto (sin Fresa). Si la
    # búsqueda de la fuente principal no excluye el día de hoy, encuentra esa
    # misma caché incompleta primero y se detiene ahí, sin llegar nunca a
    # revisar días anteriores donde Fresa sí existe.
    monkeypatch.setattr(sipsa_fetch.settings, "sipsa_pentaho_reintento_espera_segundos", 0)

    candidato_hoy = _candidato("2026-09-17", 0)
    boletin_hoy_incompleto = BoletinSipsa(
        fecha_reporte_texto="Día 2026-09-17",
        filas=[FilaSipsa(index=0, nombre="Día 2026-09-17"), FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1900)],
    )
    # Ya quedó cacheado (por un turno anterior) exactamente el mismo boletín
    # incompleto que estamos tratando de completar ahora.
    await redis.set(
        sipsa_fetch._redis_key("2026-09-17"),
        json.dumps(sipsa_fetch._boletin_to_dict(boletin_hoy_incompleto)),
        ex=1800,
    )

    filas_viejas = [
        FilaSipsa(index=0, nombre="Día 2026-09-10"),
        FilaSipsa(index=1, nombre="Fresa", precio_corabastos=14000, precio_centroabastos=13500),
        FilaSipsa(index=2, nombre="Zanahoria", precio_corabastos=1850),
        FilaSipsa(index=3, nombre="Mango", precio_corabastos=3200),
    ]
    await redis.set(
        sipsa_fetch._redis_key("2026-09-10"),
        json.dumps(sipsa_fetch._boletin_to_dict(BoletinSipsa(fecha_reporte_texto="Día 2026-09-10", filas=filas_viejas))),
        ex=3600,
    )

    candidatos = [candidato_hoy, _candidato("2026-09-10", 7)]
    resultado = await sipsa_fetch._completar_con_fuente_principal(
        boletin_hoy_incompleto, candidato_hoy, candidatos, redis=redis, db=_DBSinNada(), http_client=_HttpClienteSiempreCaido()
    )

    assert "Fresa" in [f.nombre for f in resultado.filas]


@pytest.mark.asyncio
async def test_sigue_buscando_si_los_dias_intermedios_tambien_son_respaldo_pobre(redis, monkeypatch):
    # Caída larga: varios días recientes seguidos también quedaron resueltos
    # por el respaldo (cacheados con pocos productos, sin Fresa) durante la
    # misma ventana de caída. La búsqueda no debe conformarse con el primero
    # de esos -- debe seguir hasta uno genuinamente más completo (Pentaho).
    monkeypatch.setattr(sipsa_fetch.settings, "sipsa_pentaho_reintento_espera_segundos", 0)

    candidato_hoy = _candidato("2026-09-17", 0)
    boletin_hoy = BoletinSipsa(
        fecha_reporte_texto="Día 2026-09-17",
        filas=[FilaSipsa(index=0, nombre="Día 2026-09-17"), FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1900)],
    )

    for fecha in ("2026-09-16", "2026-09-15", "2026-09-14"):
        boletin_pobre = BoletinSipsa(
            fecha_reporte_texto=f"Día {fecha}",
            filas=[FilaSipsa(index=0, nombre=f"Día {fecha}"), FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1900)],
        )
        await redis.set(sipsa_fetch._redis_key(fecha), json.dumps(sipsa_fetch._boletin_to_dict(boletin_pobre)), ex=1800)

    filas_ricas = [
        FilaSipsa(index=0, nombre="Día 2026-09-10"),
        FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1850),
        FilaSipsa(index=2, nombre="Fresa", precio_corabastos=14000, precio_centroabastos=13500),
    ]
    await redis.set(
        sipsa_fetch._redis_key("2026-09-10"),
        json.dumps(sipsa_fetch._boletin_to_dict(BoletinSipsa(fecha_reporte_texto="Día 2026-09-10", filas=filas_ricas))),
        ex=3600,
    )

    candidatos = [
        candidato_hoy,
        _candidato("2026-09-16", 1),
        _candidato("2026-09-15", 2),
        _candidato("2026-09-14", 3),
        _candidato("2026-09-10", 7),
    ]

    resultado = await sipsa_fetch._completar_con_fuente_principal(
        boletin_hoy, candidato_hoy, candidatos, redis=redis, db=_DBSinNada(), http_client=_HttpClienteSiempreCaido()
    )

    assert "Fresa" in [f.nombre for f in resultado.filas]


@pytest.mark.asyncio
async def test_sin_nada_guardado_y_pentaho_caido_no_completa_nada_pero_no_revienta(redis, monkeypatch):
    monkeypatch.setattr(sipsa_fetch.settings, "sipsa_pentaho_reintento_espera_segundos", 0)

    candidato_hoy = _candidato("2026-09-17", 0)
    boletin_hoy = BoletinSipsa(
        fecha_reporte_texto="Día 2026-09-17",
        filas=[FilaSipsa(index=0, nombre="Día 2026-09-17"), FilaSipsa(index=1, nombre="Zanahoria", precio_corabastos=1900)],
    )
    candidatos = [candidato_hoy]

    resultado = await sipsa_fetch._completar_con_fuente_principal(
        boletin_hoy, candidato_hoy, candidatos, redis=redis, db=_DBSinNada(), http_client=_HttpClienteSiempreCaido()
    )

    assert resultado is boletin_hoy
