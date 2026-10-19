from app.services.webhook_parser import extract_incoming_messages

PAYLOAD_ESTANDAR = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "123"},
                        "contacts": [{"profile": {"name": "Ana"}, "wa_id": "573001234567"}],
                        "messages": [
                            {"from": "573001234567", "id": "wamid.1", "type": "text", "text": {"body": "papa"}}
                        ],
                    },
                }
            ]
        }
    ],
}

# Formato observado en producción (sep-2026): usuario que le escribe al
# negocio sin compartir su número real -- ver docstring de webhook_parser.py.
PAYLOAD_SIN_NUMERO = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "123"},
                        "contacts": [
                            {"profile": {"name": "Marcela", "username": "marcelarincons"}, "user_id": "CO.1541771343931331"}
                        ],
                        "messages": [
                            {
                                "from_user_id": "CO.1541771343931331",
                                "id": "wamid.2",
                                "type": "text",
                                "text": {"body": "Hola"},
                            }
                        ],
                    },
                }
            ]
        }
    ],
}

PAYLOAD_SOLO_STATUS = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "123"},
                        "statuses": [{"id": "wamid.3", "status": "read"}],
                    },
                }
            ]
        }
    ],
}


def test_formato_estandar_extrae_numero_real():
    mensajes = extract_incoming_messages(PAYLOAD_ESTANDAR)
    assert len(mensajes) == 1
    assert mensajes[0].phone == "573001234567"
    assert mensajes[0].profile_name == "Ana"
    assert mensajes[0].text == "papa"


def test_formato_sin_numero_usa_user_id_como_identificador():
    # Antes del fix, esto daba phone="" y el webhook lo descartaba en
    # silencio (nunca se marcaba como leído ni se respondía).
    mensajes = extract_incoming_messages(PAYLOAD_SIN_NUMERO)
    assert len(mensajes) == 1
    assert mensajes[0].phone == "CO.1541771343931331"
    assert mensajes[0].profile_name == "Marcela"
    assert mensajes[0].text == "Hola"


def test_status_sin_mensajes_no_produce_incoming_message():
    assert extract_incoming_messages(PAYLOAD_SOLO_STATUS) == []


def test_payload_vacio_no_revienta():
    assert extract_incoming_messages({}) == []
