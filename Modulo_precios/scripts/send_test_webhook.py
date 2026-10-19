"""Simula un webhook entrante de WhatsApp Cloud API contra el servidor local,
para probar el flujo completo sin necesitar un número real de WhatsApp ni
que Meta esté configurado todavía.

Uso:
    python scripts/send_test_webhook.py "hola"
    python scripts/send_test_webhook.py "papa"
    python scripts/send_test_webhook.py "precio de la papa ayer"
"""
from __future__ import annotations

import sys
import time
import uuid

import httpx

URL = "http://localhost:8000/webhook/whatsapp"


def build_payload(texto: str, telefono: str = "573001112233") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "573000000000", "phone_number_id": "0"},
                            "contacts": [{"profile": {"name": "Agricultor de prueba"}, "wa_id": telefono}],
                            "messages": [
                                {
                                    "from": telefono,
                                    "id": f"wamid.TEST.{uuid.uuid4()}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


if __name__ == "__main__":
    texto = sys.argv[1] if len(sys.argv) > 1 else "hola"
    payload = build_payload(texto)
    resp = httpx.post(URL, json=payload, timeout=10.0)
    print(resp.status_code, resp.json())
