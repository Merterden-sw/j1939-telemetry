"""API istek/yanit semalari (Pydantic v2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SpeedCommand(BaseModel):
    """Tek araca hiz enjeksiyonu (SPN 84)."""

    speed_kmh: float = Field(..., ge=0, le=250.996, description="Hedef hiz (km/h)")
    instant: bool = Field(False, description="True ise ivme rampasi atlanir")


class PedalCommand(BaseModel):
    """Gaz (SPN 91) veya fren (SPN 521) pedali konumu."""

    pedal_pct: float = Field(..., ge=0, le=100, description="Pedal konumu (%)")


class GearRangeCommand(BaseModel):
    """Vites kademesi (SPN 162/163)."""

    gear_range: Literal["P", "R", "N", "D"]


class GearCommand(BaseModel):
    """Vites numarasi (SPN 523/524). -1 geri, 0 bos, 1+ ileri."""

    gear: int = Field(..., ge=-1, le=18)


class BatteryCommand(BaseModel):
    """Batarya doluluk (SPN 5464) ve saglik (SPN 5465)."""

    soc_pct: float | None = Field(None, ge=0, le=100)
    soh_pct: float | None = Field(None, ge=0, le=100)

    @model_validator(mode="after")
    def _en_az_biri(self) -> BatteryCommand:
        if self.soc_pct is None and self.soh_pct is None:
            raise ValueError("soc_pct veya soh_pct alanlarindan en az biri verilmeli")
        return self


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
    """Durum degistirmeden istenen PGN icin cerceve onizlemesi."""

    pgn: int = Field(65265, description="Kayit defterindeki PGN")
    source_address: int = Field(0, ge=0, le=255)
    priority: int | None = Field(None, ge=0, le=7)
    signals: dict = Field(
        default_factory=dict,
        description='Mesaja ozgu sinyal degerleri, ornek: {"speed_kmh": 60}',
    )


class DecodeRequest(BaseModel):
    """candump bicimindeki bir cerceveyi cozer: 18FEF100#F3003C0000FF1FFF"""

    frame: str = Field(..., min_length=10, examples=["18FEF100#F3003C0000FF1FFF"])
