import datetime as dt

from app.services.sipsa_dates import detectar_fecha_solicitada, generar_candidatos

# Martes 11 de agosto de 2026: misma fecha del boletín real usado como fixture.
FECHA_BASE = dt.date(2026, 8, 11)


def test_hoy():
    r = detectar_fecha_solicitada("precio de la papa hoy", FECHA_BASE)
    assert r.tiene_fecha is True
    assert r.fecha == FECHA_BASE


def test_ayer():
    r = detectar_fecha_solicitada("cuanto vale el tomate ayer", FECHA_BASE)
    assert r.fecha == FECHA_BASE - dt.timedelta(days=1)


def test_anteayer_no_se_confunde_con_ayer():
    r = detectar_fecha_solicitada("precio antes de ayer del platano", FECHA_BASE)
    assert r.fecha == FECHA_BASE - dt.timedelta(days=2)


def test_hace_dos_dias_en_palabras():
    r = detectar_fecha_solicitada("precio de la papa hace dos dias", FECHA_BASE)
    assert r.fecha == FECHA_BASE - dt.timedelta(days=2)


def test_hace_n_dias_numerico():
    r = detectar_fecha_solicitada("precio hace 5 dias", FECHA_BASE)
    assert r.fecha == FECHA_BASE - dt.timedelta(days=5)


def test_fecha_numerica_dd_mm_yyyy():
    r = detectar_fecha_solicitada("precio del 05/08/2026", FECHA_BASE)
    assert r.fecha == dt.date(2026, 8, 5)


def test_fecha_textual():
    r = detectar_fecha_solicitada("precio del 5 de agosto de 2026", FECHA_BASE)
    assert r.fecha == dt.date(2026, 8, 5)


def test_dia_semana_pasado():
    # FECHA_BASE es martes 11; "el lunes" debería resolver al lunes anterior (10)
    r = detectar_fecha_solicitada("precio de la papa el lunes", FECHA_BASE)
    assert r.fecha == dt.date(2026, 8, 10)


def test_sin_fecha():
    r = detectar_fecha_solicitada("precio de la papa", FECHA_BASE)
    assert r.tiene_fecha is False


def test_generar_candidatos_sin_fecha_produce_urls_dane():
    candidatos = generar_candidatos("precio de la papa")
    assert len(candidatos) > 0
    assert all("dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-" in c.url_sipsa for c in candidatos)
    assert candidatos[0].modo_busqueda_fecha == "mas_reciente"


def test_generar_candidatos_con_fecha_especifica():
    candidatos = generar_candidatos("precio de la papa el 05/08/2026")
    assert all(c.fecha_solicitada is True for c in candidatos)
    assert any("05ago2026" in c.url_sipsa for c in candidatos)
