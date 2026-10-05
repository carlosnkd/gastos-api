import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

TZ = ZoneInfo(os.getenv("TZ_LOCAL", "America/Mexico_City"))


def ahora() -> datetime:
    return datetime.now(TZ)


def a_local(dt: datetime) -> datetime:
    """Fechas sin zona se asumen hora local (TZ_LOCAL); todo se maneja en hora local."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


class Tipo(str, Enum):
    gasto = "gasto"
    ingreso = "ingreso"
    transferencia = "transferencia"


class Fuente(str, Enum):
    applepay = "applepay"
    manual = "manual"
    sms = "sms"
    texto = "texto"  # lenguaje natural parseado por LLM


def parse_monto(value) -> Decimal:
    """Acepta 123.4, "123.4", "$1,234.50", "MX$1,234.50", "1.234,50 €"."""
    if isinstance(value, (int, float, Decimal)):
        return abs(Decimal(str(value))).quantize(Decimal("0.01"))
    s = re.sub(r"[^\d.,-]", "", str(value))
    if not s:
        raise ValueError(f"Monto inválido: {value!r}")
    # Si la coma va después del último punto, la coma es el decimal
    if "," in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return abs(Decimal(s)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(f"Monto inválido: {value!r}")


class MovimientoBase(BaseModel):
    tipo: Tipo = Tipo.gasto
    monto: Decimal
    moneda: str = "MXN"
    comercio: Optional[str] = None
    categoria: Optional[str] = None
    cuenta: Optional[str] = None
    fuente: Fuente = Fuente.manual
    nota: Optional[str] = None
    fecha: datetime = Field(default_factory=ahora)

    @field_validator("monto", mode="before")
    @classmethod
    def _limpiar_monto(cls, v):
        return parse_monto(v)

    @field_validator("fecha", mode="after")
    @classmethod
    def _fecha_local(cls, v: datetime):
        return a_local(v)


class MovimientoCreate(MovimientoBase):
    pass


class Movimiento(MovimientoBase):
    id: str
    fila: Optional[int] = Field(default=None, exclude=True)  # fila en la hoja, uso interno


class MovimientoUpdate(BaseModel):
    tipo: Optional[Tipo] = None
    categoria: Optional[str] = None
    cuenta: Optional[str] = None
    nota: Optional[str] = None


class TextoIn(BaseModel):
    texto: str
    fuente: Fuente = Fuente.texto
