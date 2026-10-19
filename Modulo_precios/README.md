# MercoIA — Módulo de precios (WhatsApp + DANE/SIPSA)

Asistente de WhatsApp para agricultores colombianos: consulta en lenguaje
natural los precios mayoristas oficiales del DANE (boletín diario SIPSA) y
responde por WhatsApp de forma clara, con manejo de fechas relativas
("ayer", "hace 2 días", "el martes pasado"), desambiguación de productos
("papa" -> ¿negra o criolla?) y corrección de errores de escritura vía LLM.

Es la reimplementación en Python de tu flujo de n8n `MercoIA - Modulo de
precios`, pensada para correr en tu propia máquina como si fuera el
servidor, usando **WhatsApp Cloud API de Meta** directamente (sin
intermediarios tipo Evolution API/Twilio, sin automatizar WhatsApp Web).

---

## 1. Por qué WhatsApp Cloud API (y cómo se mantiene estable 24/7)

Esto responde a tu pregunta original de cómo conectar con WhatsApp de forma
robusta:

- **Meta aloja la infraestructura de WhatsApp.** Tu servidor sólo expone un
  endpoint HTTPS (`/webhook/whatsapp`) que recibe eventos y llama a la
  Graph API para responder. No hay sesión de WhatsApp Web que se pueda
  desconectar, ni QR que expire, ni librerías no oficiales (Baileys,
  whatsapp-web.js, Selenium) que se rompan con cada actualización de
  WhatsApp.
- **El webhook nunca depende de que el DANE esté disponible.** Cada boletín
  SIPSA que se descarga se guarda en **Redis** (caché rápida, ~20 h) y en
  **PostgreSQL** (persistente). Si el DANE está caído o cambia de ruta
  temporalmente, MercoIA sigue respondiendo con la última base ya conocida
  en vez de fallar la conversación completa.
- **El webhook responde en milisegundos.** FastAPI valida y encola el
  mensaje como tarea en segundo plano (`BackgroundTasks`) y responde `200`
  de inmediato; el trabajo pesado (bajar el Excel, llamar al LLM) ocurre
  después, sin bloquear ni arriesgar que Meta reintente el webhook.
- **Deduplicación de reintentos.** Si Meta reenvía el mismo evento (pasa
  cuando tu servidor tarda en responder), MercoIA lo detecta por
  `message_id` y no vuelve a contestar dos veces.
- **`restart: unless-stopped` + healthchecks** en `docker-compose.yml`: si
  el proceso muere o la máquina se reinicia, los tres contenedores
  (app, Postgres, Redis) vuelven a levantarse solos.
- **Próximo paso natural para producción real** (no incluido aún, para no
  sobre-construir esta primera versión): mover de tu máquina a un
  VPS/servicio administrado (DigitalOcean, Render, Railway...) con un
  dominio propio y HTTPS de verdad, y cambiar `BackgroundTasks` por una
  cola de tareas (RQ/Celery) si el volumen de mensajes crece mucho. La
  arquitectura ya está separada en capas (routers / services / db) para que
  ese salto sea sencillo cuando llegue el momento.

---

## 2. Arquitectura

```
Agricultor (WhatsApp)
        │
        ▼
WhatsApp Cloud API (Meta) ──── webhook HTTPS ────▶ FastAPI (este proyecto)
        ▲                                                │
        │ Graph API (enviar texto)                       ▼
        └──────────────────────────────────────  app/services/conversation.py
                                                           │
                     ┌─────────────────────────────────────┼───────────────────────────┐
                     ▼                                     ▼                            ▼
              clasificar saludo/                    detectar fecha +              LLM normalizador
              despedida/consulta                    descargar/parsear             (OpenAI) escoge el
              (classifier.py)                       boletín SIPSA                  producto exacto de
                     │                               (sipsa_dates.py,               la lista del día
                     ▼                                sipsa_fetch.py,                     │
              desambiguar producto                    sipsa_parser.py)                    ▼
              papa/plátano, con                             │                     construir mensaje final
              contexto en Redis                             ▼                     con precios + variación
              (ambiguous.py)                          caché Redis (~20h)          (pricing.py)
                                                       + snapshot en Postgres
```

### Mapeo respecto al flujo de n8n original

| Nodo n8n                                   | Archivo Python                              |
|---------------------------------------------|----------------------------------------------|
| Webhook2 / Variables3                       | `app/routers/webhook.py` + `webhook_parser.py` |
| Code: Detectar saludo / Switch              | `app/services/classifier.py`                  |
| Code: Detectar producto ambiguo / If        | `app/services/ambiguous.py` (usa Redis en vez de workflow static data) |
| Generar URLs SIPSA candidatas               | `app/services/sipsa_dates.py`                 |
| Loop Over Items / HTTP Request              | `app/services/sipsa_fetch.py` (+ caché Redis/Postgres) |
| Descargar XLSX1 / Leer XLSX1                | `app/services/sipsa_parser.py`                |
| Detectar nombre correcto / Agente Normalizador | `app/services/normalizer.py` (+ `memory.py` para el historial) |
| Code: obtener precios reales                | `app/services/pricing.py`                     |
| Send Text (todas las variantes)             | `app/services/whatsapp_client.py`             |
| Orquestación completa del grafo             | `app/services/conversation.py`                |

El parser de Excel (`sipsa_parser.py`) se validó contra boletines reales
descargados de `dane.gov.co` (no es un mapeo de columnas "a ciegas"): ubica
dinámicamente las columnas de Bogotá-Corabastos y Bucaramanga-Centroabastos
buscando esos textos en la fila de encabezados, así que sigue funcionando
aunque el DANE reordene o agregue mercados.

---

## 3. Prerrequisitos

- Docker + Docker Compose (ya los tienes instalados).
- Una cuenta en [developers.facebook.com](https://developers.facebook.com)
  con una App de tipo *Business* y el producto **WhatsApp** agregado
  (siguiendo la conversación del PDF que adjuntaste: *Connect with
  customers through WhatsApp* → crear Business Portfolio → obtener número
  de prueba). Al final de ese proceso tendrás:
  - `WHATSAPP_TOKEN` (access token, temporal de 24h para pruebas o
    permanente vía System User),
  - `WHATSAPP_PHONE_NUMBER_ID` (Panel → WhatsApp → API Setup),
  - un número de prueba de WhatsApp ya vinculado.
- Una API key de OpenAI (para el normalizador de nombres de producto).
- Una forma de exponer tu `localhost:8000` a internet con HTTPS, porque
  Meta **exige** una URL pública para el webhook y tú vas a correr el
  servidor en tu propia máquina. Recomendado: [ngrok](https://ngrok.com)
  (`ngrok http 8000`) o [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
  (`cloudflared tunnel --url http://localhost:8000`). Cualquiera de los dos
  te da gratis una URL `https://algo.ngrok-free.app` o
  `https://algo.trycloudflare.com`.

---

## 4. Puesta en marcha

```bash
cd /home/oem/D/WebSites/MercoIA

# 1. Copia y completa tus credenciales
cp .env.example .env
nano .env   # completa WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
            # WHATSAPP_VERIFY_TOKEN (inventa un secreto), OPENAI_API_KEY

# 2. Levanta Postgres + Redis + la app
docker compose up -d --build
# (si tu Docker no trae el plugin "compose", usa: docker-compose up -d --build)

# 3. Verifica que todo esté sano
curl http://localhost:8000/health
# {"status":"ok","postgres":"ok","redis":"ok"}
```

La app queda escuchando en `http://localhost:8000`, con recarga automática
al editar código (montado como volumen) y reinicio automático si se cae.

### Exponer el webhook a internet (necesario para que Meta te hable)

```bash
ngrok http 8000
# copia la URL https que te da, ej: https://a1b2c3d4.ngrok-free.app
```

### Configurar el webhook en Meta

En tu App de Meta → WhatsApp → Configuration → Webhook:

- **Callback URL:** `https://a1b2c3d4.ngrok-free.app/webhook/whatsapp`
- **Verify token:** el mismo valor que pusiste en `WHATSAPP_VERIFY_TOKEN`
- Click **Verify and save** (debe responder `200` — MercoIA implementa este
  handshake en `GET /webhook/whatsapp`).
- En **Webhook fields**, suscríbete a `messages`.

A partir de aquí, cualquier mensaje que envíes al número de prueba de
WhatsApp llegará a tu máquina y MercoIA te responderá.

> Nota: mientras uses ngrok/cloudflared gratis, la URL cambia cada vez que
> reinicias el túnel, así que tendrás que actualizarla en el panel de Meta.
> Para una URL fija sin pagar, `cloudflared tunnel` con un dominio propio
> (gratis) es una alternativa; para producción real, lo natural es mover el
> despliegue a un servidor con dominio propio.

---

## 5. Probar sin depender de un número real de WhatsApp

Incluí un script que simula exactamente el payload que envía Meta, para que
puedas validar todo el flujo (clasificación, desambiguación, descarga real
del DANE, LLM, formato del mensaje) sin tener el webhook público todavía:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d   # necesitas Postgres/Redis/la app corriendo

python scripts/send_test_webhook.py "hola"
python scripts/send_test_webhook.py "papa"              # -> pide aclarar (negra/criolla)
python scripts/send_test_webhook.py "papa negra"        # -> responde con precio real
python scripts/send_test_webhook.py "precio del tomate ayer"
```

Como en pruebas locales normalmente `WHATSAPP_TOKEN` todavía no es válido,
verás en los logs (`docker logs mercoia_app -f`) que MercoIA sí procesó todo
correctamente y sólo falla el último paso (el envío real a Graph API) con
un `401` — apenas pongas credenciales reales, ese último paso también
funciona. Puedes revisar la respuesta que se iba a enviar directamente en
la base de datos:

```bash
docker exec -it mercoia_postgres psql -U mercoia -d mercoia \
  -c "SELECT direction, message_type, content FROM conversation_logs ORDER BY id DESC LIMIT 5;"
```

### Tests automáticos

```bash
source .venv/bin/activate
pytest -q
```

Incluye un boletín SIPSA **real** (`tests/fixtures/sipsa_sample.xlsx`,
descargado de dane.gov.co) como fixture, para que el parser y el cálculo de
precios se prueben contra datos reales y no contra un mock inventado.

---

## 6. Variables de entorno

Ver `.env.example` para la lista completa y comentada. Las más importantes:

| Variable | Qué es |
|---|---|
| `WHATSAPP_TOKEN` | Access token de la Graph API |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número (no es el número telefónico) |
| `WHATSAPP_VERIFY_TOKEN` | Secreto que tú inventas, para el handshake del webhook |
| `WHATSAPP_APP_SECRET` | Para validar la firma `X-Hub-Signature-256` de cada webhook (recomendado antes de producción) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | Normalizador de nombres de producto |
| `SIPSA_MAX_DIAS_ATRAS` | Cuántos días hacia atrás buscar si no hay boletín de hoy (por defecto 20) |

---

## 7. Estructura del proyecto

```
app/
  main.py                 # arranque FastAPI, lifespan (init DB, cliente HTTP)
  config.py                # settings (pydantic-settings, lee .env)
  db.py / models.py        # PostgreSQL (SQLAlchemy async)
  redis_client.py          # Redis compartido
  http_client.py           # cliente httpx compartido
  routers/
    webhook.py              # GET verify / POST recibir mensajes
    health.py
  services/
    classifier.py           # saludo / despedida / consulta
    ambiguous.py             # papa negra vs criolla, plátano guineo vs hartón
    sipsa_dates.py           # "ayer", "hace 2 días", "10 de junio"... -> URL del DANE
    sipsa_fetch.py           # descarga + caché (Redis) + persistencia (Postgres)
    sipsa_parser.py          # parseo real del .xlsx del DANE
    normalizer.py            # LLM: nombre libre -> producto exacto de la lista
    pricing.py               # arma el mensaje final con precios y variación
    memory.py                # memoria corta de conversación por teléfono
    dedup.py                 # evita responder dos veces al mismo webhook
    whatsapp_client.py       # envío de mensajes vía Graph API
    webhook_parser.py        # parseo del payload de Meta
    conversation.py          # orquestador (equivalente al grafo de n8n)
tests/                       # 35 tests, incluye fixture real del DANE
scripts/send_test_webhook.py # simulador de webhook para pruebas locales
```

---

## 8. Próximos pasos sugeridos (no implementados todavía, a propósito)

Siguiendo el mismo orden de prioridades del PDF que compartiste:

1. **Alembic** para versionar el esquema de PostgreSQL en vez de
   `create_all()` (hoy suficiente porque el esquema es chico y estable).
2. **Cola de tareas real (RQ/Celery)** en vez de `BackgroundTasks` si el
   volumen de agricultores crece mucho — `BackgroundTasks` es perfecto para
   un piloto de cientos de usuarios corriendo en un solo proceso.
3. **Mover a un servidor con dominio propio** (DigitalOcean/Render/Railway)
   con HTTPS real vía Let's Encrypt/Cloudflare, para no depender de un
   túnel ngrok cuando quieras que el servicio quede 24/7 sin tu laptop
   encendida.
4. **Token permanente (System User)** de Meta en vez del token temporal de
   24h, y `WHATSAPP_APP_SECRET` obligatorio para validar firmas.
5. **Notas/tag de mensajes template** si algún día necesitas iniciar tú la
   conversación (recordatorios de precio) fuera de la ventana de 24h de
   servicio gratuito — eso requiere plantillas aprobadas por Meta y tiene
   costo por mensaje.
6. Consultas por **voz** (Whisper) y comparación entre **todos los
   mercados** del boletín (el parser ya extrae los 10 mercados, hoy sólo se
   usan Bogotá y Bucaramanga para mantener el mismo formato de respuesta
   del flujo original).

---

## 9. Troubleshooting

- **Meta no logra verificar el webhook:** revisa que `WHATSAPP_VERIFY_TOKEN`
  en `.env` sea idéntico al que escribiste en el panel de Meta, y que la
  URL de ngrok/cloudflared apunte a `/webhook/whatsapp` (no a `/`).
- **401 al enviar mensajes:** el `WHATSAPP_TOKEN` expiró (los tokens de
  prueba duran 24h) o es inválido; genera uno nuevo en el panel.
- **"No pude encontrar una base SIPSA Diario reciente":** el DANE no ha
  publicado el boletín todavía (se actualiza ~2:00 p.m. hora Colombia) o
  cambió la ruta del archivo; revisa `docker logs mercoia_app` para ver
  qué URLs se intentaron.
- **El LLM no encuentra el producto:** revisa que `OPENAI_API_KEY` sea
  válida (`docker logs mercoia_app` mostrará `AuthenticationError` si no lo
  es).
