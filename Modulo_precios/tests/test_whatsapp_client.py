"""Tests del payload que arma WhatsAppClient.send_text según el tipo de
identificador (wa_id real vs. BSUID) -- ver el docstring de whatsapp_client.py
para el contexto del bug real que motivó esto. No se testea contra la API
real de Meta, solo que se arme el payload correcto."""
import pytest

from app.services.whatsapp_client import WhatsAppClient, _es_bsuid


class _RespuestaFalsa:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        pass


class _HttpClientFalso:
    def __init__(self):
        self.ultima_llamada: dict | None = None

    async def post(self, url, *, json, headers, timeout):
        self.ultima_llamada = json
        return _RespuestaFalsa()


def test_es_bsuid():
    assert _es_bsuid("CO.1541771343931331") is True
    assert _es_bsuid("US.13491208655302741918") is True
    assert _es_bsuid("573001234567") is False


@pytest.mark.asyncio
async def test_send_text_a_numero_real_usa_campo_to():
    http = _HttpClientFalso()
    await WhatsAppClient(http).send_text("573001234567", "hola", delay=0)

    assert http.ultima_llamada["to"] == "573001234567"
    assert "recipient" not in http.ultima_llamada


@pytest.mark.asyncio
async def test_send_text_a_bsuid_usa_campo_recipient():
    # Antes de este fix se enviaba con "to", y la Graph API respondía la
    # petición con éxito pero luego la entrega fallaba con el error 131026
    # "Message undeliverable" (confirmado en producción, ver ngrok).
    http = _HttpClientFalso()
    await WhatsAppClient(http).send_text("CO.1541771343931331", "hola", delay=0)

    assert http.ultima_llamada["recipient"] == "CO.1541771343931331"
    assert http.ultima_llamada["recipient_type"] == "individual"
    assert "to" not in http.ultima_llamada
