"""
J1939 protokol ilkelleri: 29-bit tanimlayici, bit paketleme, olcekleme, cerceve.

Bu modul mesaj tanimlarindan bagimsizdir; yalnizca J1939-21'in tasima katmani
kurallarini ve SPN degerlerinin ham byte'lara donusum matematigini bilir.

29-bit CAN ID yerlesimi (J1939-21):

    bit 28..26 : Priority
    bit 25     : EDP  (Extended Data Page)
    bit 24     : DP   (Data Page)
    bit 23..16 : PF   (PDU Format)
    bit 15..8  : PS   (PDU Specific / Group Extension veya Hedef Adres)
    bit 7..0   : SA   (Source Address)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --------------------------------------------------------------------------- #
# Genel sabitler
# --------------------------------------------------------------------------- #

DEFAULT_PRIORITY: Final[int] = 6
PDU1_MAX_PF: Final[int] = 239
GLOBAL_ADDRESS: Final[int] = 0xFF
NULL_ADDRESS: Final[int] = 0xFE

# 2-bit ayrik parametre degerleri (J1939-71)
BIT2_OFF: Final[int] = 0b00
BIT2_ON: Final[int] = 0b01
BIT2_ERROR: Final[int] = 0b10
BIT2_NOT_AVAILABLE: Final[int] = 0b11

# Tek byte'lik olculen parametrelerde ayrilmis degerler
BYTE_ERROR: Final[int] = 0xFE
BYTE_NOT_AVAILABLE: Final[int] = 0xFF
BYTE_MAX_VALID: Final[int] = 0xFA  # 250

# Iki byte'lik olculen parametrelerde ayrilmis degerler
WORD_ERROR: Final[int] = 0xFE00
WORD_NOT_AVAILABLE: Final[int] = 0xFFFF
WORD_MAX_VALID: Final[int] = 0xFAFF  # 64255


class J1939Error(ValueError):
    """Gecersiz J1939 parametresi."""


# --------------------------------------------------------------------------- #
# CAN Identifier
# --------------------------------------------------------------------------- #


def build_can_id(
    pgn: int,
    source_address: int = 0,
    priority: int = DEFAULT_PRIORITY,
    destination_address: int | None = None,
) -> int:
    """PGN + kaynak adresten 29-bit genisletilmis CAN ID uretir."""
    if not 0 <= priority <= 7:
        raise J1939Error(f"Priority 0-7 araliginda olmali: {priority}")
    if not 0 <= source_address <= 0xFF:
        raise J1939Error(f"Source address 0-255 araliginda olmali: {source_address}")
    if not 0 <= pgn <= 0x3FFFF:
        raise J1939Error(f"PGN 0-262143 araliginda olmali: {pgn}")

    edp = (pgn >> 17) & 0x01
    dp = (pgn >> 16) & 0x01
    pf = (pgn >> 8) & 0xFF
    ps = pgn & 0xFF

    if pf <= PDU1_MAX_PF:
        # PDU1: PS alani hedef adres olarak kullanilir.
        ps = GLOBAL_ADDRESS if destination_address is None else destination_address
        if not 0 <= ps <= 0xFF:
            raise J1939Error(f"Destination address 0-255 araliginda olmali: {ps}")

    return (
        (priority & 0x07) << 26
        | edp << 25
        | dp << 24
        | pf << 16
        | ps << 8
        | (source_address & 0xFF)
    )


def decode_can_id(can_id: int) -> dict:
    """29-bit CAN ID'yi J1939 alanlarina ayristirir."""
    if not 0 <= can_id <= 0x1FFFFFFF:
        raise J1939Error(f"29-bit disi CAN ID: {can_id:#x}")

    priority = (can_id >> 26) & 0x07
    edp = (can_id >> 25) & 0x01
    dp = (can_id >> 24) & 0x01
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    sa = can_id & 0xFF

    is_pdu1 = pf <= PDU1_MAX_PF
    pgn = (edp << 17) | (dp << 16) | (pf << 8) | (0x00 if is_pdu1 else ps)

    return {
        "can_id": can_id,
        "can_id_hex": format_can_id(can_id),
        "priority": priority,
        "extended_data_page": edp,
        "data_page": dp,
        "pdu_format": pf,
        "pdu_specific": ps,
        "pdu_type": "PDU1" if is_pdu1 else "PDU2",
        "destination_address": ps if is_pdu1 else None,
        "source_address": sa,
        "pgn": pgn,
        "pgn_hex": f"0x{pgn:04X}",
    }


def format_can_id(can_id: int) -> str:
    """CAN ID'yi candump uyumlu 8 haneli buyuk harf hex olarak dondurur."""
    return f"{can_id:08X}"


# --------------------------------------------------------------------------- #
# Bit paketleme
# --------------------------------------------------------------------------- #


def pack_2bit(b12: int, b34: int, b56: int, b78: int) -> int:
    """Dort adet 2-bit parametreyi tek byte'a paketler (bit 1-2 en dusuk anlamli)."""
    for value in (b12, b34, b56, b78):
        if not 0 <= value <= 3:
            raise J1939Error(f"2-bit deger 0-3 araliginda olmali: {value}")
    return (b12 & 0x3) | ((b34 & 0x3) << 2) | ((b56 & 0x3) << 4) | ((b78 & 0x3) << 6)


def unpack_2bit(byte_value: int) -> tuple[int, int, int, int]:
    """pack_2bit islemini tersine cevirir."""
    return (
        byte_value & 0x3,
        (byte_value >> 2) & 0x3,
        (byte_value >> 4) & 0x3,
        (byte_value >> 6) & 0x3,
    )


# --------------------------------------------------------------------------- #
# SPN olcekleme (resolution / offset)
# --------------------------------------------------------------------------- #


def encode_scaled(
    value: float | None,
    *,
    resolution: float,
    offset: float = 0.0,
    byte_length: int = 1,
    max_valid: int | None = None,
) -> int:
    """
    Fiziksel degeri SPN ham degerine cevirir.

        raw = round((value - offset) / resolution)

    None verilirse "not available" isareti dondurulur. Aralik disi degerler
    gecerli ust sinira kirpilir.
    """
    na = BYTE_NOT_AVAILABLE if byte_length == 1 else WORD_NOT_AVAILABLE
    if value is None:
        return na

    ceiling = (
        max_valid
        if max_valid is not None
        else (BYTE_MAX_VALID if byte_length == 1 else WORD_MAX_VALID)
    )
    raw = int(round((float(value) - offset) / resolution))
    return min(max(raw, 0), ceiling)


def decode_scaled(
    raw: int,
    *,
    resolution: float,
    offset: float = 0.0,
    byte_length: int = 1,
    digits: int = 3,
) -> float | None:
    """SPN ham degerini fiziksel degere cevirir; hata/veri-yok durumunda None."""
    error_floor = BYTE_ERROR if byte_length == 1 else WORD_ERROR
    if raw >= error_floor:
        return None
    return round(raw * resolution + offset, digits)


# --------------------------------------------------------------------------- #
# Cerceve
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class J1939Frame:
    """Tek bir J1939 CAN cercevesi (29-bit ID + veri alani)."""

    can_id: int
    data: bytes
    pgn: int
    acronym: str = ""
    priority: int = DEFAULT_PRIORITY
    source_address: int = 0
    timestamp: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def can_id_hex(self) -> str:
        return format_can_id(self.can_id)

    @property
    def data_hex(self) -> str:
        return self.data.hex().upper()

    @property
    def candump(self) -> str:
        """candump/cansend bicimi: 18FEF100#F3003C0000FF1FFF"""
        return f"{self.can_id_hex}#{self.data_hex}"

    @property
    def data_bytes_hex(self) -> list[str]:
        return [f"{b:02X}" for b in self.data]

    def to_dict(self) -> dict:
        return {
            "can_id": self.can_id,
            "can_id_hex": self.can_id_hex,
            "data_hex": self.data_hex,
            "data_bytes": self.data_bytes_hex,
            "candump": self.candump,
            "pgn": self.pgn,
            "pgn_hex": f"0x{self.pgn:04X}",
            "acronym": self.acronym,
            "priority": self.priority,
            "source_address": self.source_address,
            "dlc": len(self.data),
            "timestamp": self.timestamp,
            **self.meta,
        }
