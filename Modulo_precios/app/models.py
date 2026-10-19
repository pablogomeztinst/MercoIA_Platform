"""Modelos de PostgreSQL.

Se mantiene el esquema deliberadamente pequeño para esta v1:

- ProcessedMessage: deduplicación de webhooks (Meta puede reenviar el mismo evento).
- ConversationLog:  historial de mensajes entrantes/salientes (auditoría / debug).
- SipsaSnapshot:    copia normalizada de cada boletín diario SIPSA ya descargado,
                    para no depender de que el DANE esté disponible en cada consulta
                    y para no volver a descargar/parsear el mismo Excel dos veces.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    phone: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # "in" | "out"
    message_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SipsaSnapshot(Base):
    __tablename__ = "sipsa_snapshots"
    __table_args__ = (Index("ix_sipsa_snapshots_fecha_iso", "fecha_objetivo_iso"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fecha_dane_key: Mapped[str] = mapped_column(String(32), unique=True)
    fecha_objetivo_iso: Mapped[str] = mapped_column(String(10))
    fecha_reporte_texto: Mapped[str] = mapped_column(String(128))
    url_sipsa: Mapped[str] = mapped_column(String(256))
    # Lista completa de filas parseadas del Excel (todas las categorías/mercados),
    # tal como las produce app.services.sipsa_parser.parse_workbook().
    rows: Mapped[list] = mapped_column(JSON)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
