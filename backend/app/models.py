"""API istek/yanit semalari (Pydantic v2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SpeedCommand(BaseModel):
    """Tek araca hiz enjeksiyonu."""

    speed_kmh: float = Field(..., ge=0, le=250.996, description="Hedef hiz (km/h)")
    instant: bool = Field(False, description="True ise ivme rampasi atlanir")


class ModeCommand(BaseModel):
    mode: Literal["manual", "auto", "idle"]


class ToggleCommand(BaseModel):
    value: bool


class CruiseCommand(BaseModel):
    active: bool
    set_speed_kmh: float | None = Field(None, ge=0, le=250)


class FleetCommand(BaseModel):
    action: Literal["stop_all", "auto_all", "manual_all", "resume_all"]
    vehicle_ids: list[str] | None = None


class EncodeRequest(BaseModel):
    """Durum degistirmeden J1939 cercevesi onizlemesi."""

    speed_kmh: float | None = Field(None, ge=0, le=250.996)
    source_address: int = Field(0, ge=0, le=255)
    priority: int = Field(6, ge=0, le=7)


class DecodeRequest(BaseModel):
    """candump bicimindeki bir cerceveyi cozer: 18FEF100#F3003C0000FF1FFF"""

    frame: str = Field(..., min_length=10, examples=["18FEF100#F3003C0000FF1FFF"])
