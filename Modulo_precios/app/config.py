"""Configuración central de la aplicación (lee variables de entorno / .env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    timezone: str = "America/Bogota"

    # --- WhatsApp Cloud API ---
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_api_version: str = "v21.0"

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-nano-2025-04-14"

    # --- Infra ---
    database_url: str = "postgresql+asyncpg://mercoia:mercoia@localhost:5432/mercoia"
    redis_url: str = "redis://localhost:6379/0"

    # --- SIPSA ---
    sipsa_max_dias_atras: int = 20
    sipsa_base_url: str = (
        "https://www.dane.gov.co/files/operaciones/SIPSA/anex-SIPSADiario-{fecha}.xlsx"
    )
    sipsa_snapshot_ttl_seconds: int = 20 * 3600  # ~20h, se refresca por día hábil

    # --- SIPSA: fuente Pentaho (dashboard oficial del DANE, ver sipsa_pentaho.py) ---
    # Fuente principal por defecto; si falla para un día puntual, se intenta
    # automáticamente la otra ("excel", el .xlsx estático de siempre) como
    # respaldo antes de darse por vencido con ese día. Se puede invertir la
    # prioridad cambiando este valor a "excel" sin tocar código.
    sipsa_data_source: str = "pentaho"  # "pentaho" | "excel"
    sipsa_pentaho_url: str = "https://apps.dane.gov.co/pentaho/plugin/cda/api/doQuery"
    sipsa_pentaho_path: str = "/public/SIPSA/SIPSAV17.cda"
    # Debe ser [Corabastos, Centroabastos] en ese orden -- sipsa_pentaho.py
    # asume esa posición para mapear a precio_corabastos/precio_centroabastos.
    sipsa_pentaho_fuentes: list[str] = ["Bogotá, D.C., Corabastos", "Bucaramanga, Centroabastos"]
    sipsa_pentaho_grupos: list[str] = ["Frutas", "Tuberculos, raices y platanos", "Verduras y hortalizas"]
    sipsa_pentaho_lookback_dias: int = 10  # ventana hacia atrás para calcular la variación (ver _calcular_variacion)
    sipsa_pentaho_articulos_ttl_seconds: int = 24 * 3600  # la lista de productos por grupo casi no cambia
    # Timeout corto a propósito: esta función se puede llamar decenas de
    # veces en un solo mensaje (ver el cortacircuitos abajo); un timeout
    # largo multiplicado por muchos intentos es lo que causó respuestas
    # lentas/duplicadas en producción (incidente real, 17-sep-2026).
    sipsa_pentaho_timeout_segundos: float = 8.0
    # Un tropiezo de red/5xx se reintenta UNA vez tras esta pausa antes de
    # rendirse -- Pentaho falla de forma intermitente (un 503 suelto entre
    # respuestas normales), y sin este reintento un solo tropiezo abría el
    # cortacircuitos innecesariamente (ver sipsa_pentaho._doquery).
    sipsa_pentaho_reintento_espera_segundos: float = 1.0
    # Cuánto tiempo se evita reintentar Pentaho después de que TAMBIÉN falla
    # el reintento anterior -- ver sipsa_pentaho._circuito_abierto/_abrir_circuito.
    sipsa_pentaho_circuito_ttl_segundos: int = 90
    # Cada cuánto se refresca sola la caché del boletín más reciente en
    # segundo plano (ver sipsa_fetch.precalentar), para que ningún usuario
    # real sea el primero en pagar el costo de una caché fría o una caída
    # momentánea de Pentaho.
    sipsa_precalentar_intervalo_segundos: int = 15 * 60

    # Si la fuente de respaldo resuelve un día dentro de esta ventana (contada
    # desde hoy), se trata como "provisional" -- ver sipsa_fetch._es_respaldo_provisional:
    # no se guarda como definitivo en Postgres, porque la fuente principal
    # (Pentaho) suele publicar esos últimos días con 1-2 de rezago.
    sipsa_fallback_provisional_dias: int = 3
    sipsa_fallback_provisional_ttl_seconds: int = 30 * 60  # reintentar la fuente principal pronto

    # --- Conversación ---
    send_message_delay_seconds: float = 1.0
    ambiguous_context_ttl_seconds: int = 20 * 60  # 20 minutos, igual que el flujo n8n
    menu_pendiente_ttl_seconds: int = 30 * 60  # cuánto se espera la respuesta al menú inicial (1/2)
    memory_ttl_seconds: int = 60 * 60
    memory_window: int = 6

    @property
    def graph_api_base(self) -> str:
        return f"https://graph.facebook.com/{self.whatsapp_api_version}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
