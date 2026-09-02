"""
SAE J1939 protokol katmani.

Bu modul PGN 65265 (CCVS1 - Cruise Control / Vehicle Speed 1) mesajinin
kodlanmasi/cozulmesi ve 29-bit genisletilmis CAN tanimlayicisinin (Extended
Identifier) olusturulmasindan sorumludur. Ag/IO bagimliligi yoktur, saf
fonksiyoneldir; bu sayede birim testleri ile dogrulanabilir.

29-bit CAN ID yerlesimi (J1939-21):

    bit 28..26 : Priority        (3 bit, varsayilan 6)
    bit 25     : EDP             (Extended Data Page)
    bit 24     : DP              (Data Page)
    bit 23..16 : PF              (PDU Format)
    bit 15..8  : PS              (PDU Specific / Group Extension veya Hedef Adres)
    bit 7..0   : SA              (Source Address)

PGN 65265 = 0xFEF1 -> PF = 0xFE (>= 240, yani PDU2), PS = 0xF1 (Group Extension).
Priority 6 ve SA 0x00 icin: 0x18FEF100
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --------------------------------------------------------------------------- #
# Sabitler
# --------------------------------------------------------------------------- #

PGN_CCVS1: Final[int] = 65265  # 0xFEF1
DEFAULT_PRIORITY: Final[int] = 6
CCVS1_DLC: Final[int] = 8
CCVS1_TRANSMIT_RATE_MS: Final[int] = 100

# SPN 84 - Wheel-Based Vehicle Speed
SPN84_RESOLUTION: Final[float] = 1.0 / 256.0  # km/h / bit
SPN84_OFFSET: Final[float] = 0.0
SPN84_RAW_MAX: Final[int] = 0xFAFF  # 64255 -> 250.996 km/h
SPN84_MAX_KMH: Final[float] = SPN84_RAW_MAX * SPN84_RESOLUTION
SPN84_RAW_ERROR: Final[int] = 0xFE00  # hata gostergesi
SPN84_RAW_NOT_AVAILABLE: Final[int] = 0xFFFF  # veri yok

# SPN 86 - Cruise Control Set Speed (1 km/h / bit)
SPN86_MAX_KMH: Final[int] = 250
SPN86_NOT_AVAILABLE: Final[int] = 0xFF

# 2-bit ayrik parametre degerleri (J1939-71)
BIT2_OFF: Final[int] = 0b00
BIT2_ON: Final[int] = 0b01
BIT2_ERROR: Final[int] = 0b10
BIT2_NOT_AVAILABLE: Final[int] = 0b11

PDU1_MAX_PF: Final[int] = 239  # PF <= 239 ise PDU1 (hedef adresli)
GLOBAL_ADDRESS: Final[int] = 0xFF
NULL_ADDRESS: Final[int] = 0xFE


class J1939Error(ValueError):
    """Gecersiz J1939 parametresi."""


# --------------------------------------------------------------------------- #
# CAN Identifier
# --------------------------------------------------------------------------- #


def build_can_id(
    pgn: int = PGN_CCVS1,
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
# Bit/byte yardimcilari
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
# SPN 84 - Wheel-Based Vehicle Speed
# --------------------------------------------------------------------------- #


def encode_wheel_speed(speed_kmh: float | None) -> int:
    """
    km/h degerini SPN 84 ham (raw) degerine cevirir.

    None verilirse "not available" (0xFFFF) dondurulur. Aralik disi degerler
    0 .. 250.996 km/h araligina kirpilir.
    """
    if speed_kmh is None:
        return SPN84_RAW_NOT_AVAILABLE

    clamped = min(max(float(speed_kmh), 0.0), SPN84_MAX_KMH)
    raw = int(round((clamped - SPN84_OFFSET) / SPN84_RESOLUTION))
    return min(raw, SPN84_RAW_MAX)


def decode_wheel_speed(raw: int) -> float | None:
    """SPN 84 ham degerini km/h'ye cevirir; hata/veri-yok durumunda None."""
    if raw >= SPN84_RAW_ERROR:
        return None
    return round(raw * SPN84_RESOLUTION + SPN84_OFFSET, 3)


def wheel_speed_bytes(speed_kmh: float | None) -> tuple[int, int]:
    """SPN 84 icin (dusuk byte, yuksek byte) ciftini dondurur (little-endian)."""
    raw = encode_wheel_speed(speed_kmh)
    return raw & 0xFF, (raw >> 8) & 0xFF


# --------------------------------------------------------------------------- #
# CCVS1 (PGN 65265) veri alani
# --------------------------------------------------------------------------- #


def build_ccvs1_data(
    speed_kmh: float | None,
    *,
    parking_brake: int = BIT2_OFF,
    two_speed_axle: int = BIT2_NOT_AVAILABLE,
    cruise_pause: int = BIT2_NOT_AVAILABLE,
    park_brake_inhibit: int = BIT2_NOT_AVAILABLE,
    cruise_active: int = BIT2_OFF,
    cruise_enable: int = BIT2_OFF,
    brake_switch: int = BIT2_OFF,
    clutch_switch: int = BIT2_OFF,
    cruise_set_speed_kmh: int | None = None,
    pto_state: int = 0x1F,
    cruise_control_state: int = 0b000,
) -> bytes:
    """
    8 byte'lik CCVS1 veri alanini olusturur (J1939-71).

        Byte 1 : SPN 69 / 70 / 1633 / 3807   (2'ser bit)
        Byte 2 : SPN 84  dusuk byte
        Byte 3 : SPN 84  yuksek byte
        Byte 4 : SPN 595 / 596 / 597 / 598   (2'ser bit)
        Byte 5 : SPN 599 / 600 / 601 / 602   (2'ser bit)
        Byte 6 : SPN 86  Cruise Control Set Speed (1 km/h/bit)
        Byte 7 : SPN 976 PTO durumu (5 bit) + SPN 527 CC durumu (3 bit)
        Byte 8 : SPN 968 / 967 / 966 / 1237  (2'ser bit)
    """
    speed_lo, speed_hi = wheel_speed_bytes(speed_kmh)

    if cruise_set_speed_kmh is None:
        set_speed = SPN86_NOT_AVAILABLE
    else:
        set_speed = min(max(int(round(cruise_set_speed_kmh)), 0), SPN86_MAX_KMH)

    byte1 = pack_2bit(two_speed_axle, parking_brake, cruise_pause, park_brake_inhibit)
    byte4 = pack_2bit(cruise_active, cruise_enable, brake_switch, clutch_switch)
    byte5 = pack_2bit(BIT2_OFF, BIT2_OFF, BIT2_OFF, BIT2_OFF)
    byte7 = (pto_state & 0x1F) | ((cruise_control_state & 0x07) << 5)
    byte8 = pack_2bit(
        BIT2_NOT_AVAILABLE, BIT2_NOT_AVAILABLE, BIT2_NOT_AVAILABLE, BIT2_NOT_AVAILABLE
    )

    return bytes((byte1, speed_lo, speed_hi, byte4, byte5, set_speed, byte7, byte8))


def parse_ccvs1_data(data: bytes) -> dict:
    """CCVS1 veri alanini insan tarafindan okunabilir sozluge cevirir."""
    if len(data) != CCVS1_DLC:
        raise J1939Error(f"CCVS1 veri alani 8 byte olmali: {len(data)}")

    two_speed_axle, parking_brake, cruise_pause, park_brake_inhibit = unpack_2bit(data[0])
    cruise_active, cruise_enable, brake_switch, clutch_switch = unpack_2bit(data[3])
    raw_speed = data[1] | (data[2] << 8)
    set_speed = data[5]

    return {
        "spn_84_wheel_based_speed_kmh": decode_wheel_speed(raw_speed),
        "spn_84_raw": raw_speed,
        "spn_86_cruise_set_speed_kmh": None if set_speed == SPN86_NOT_AVAILABLE else set_speed,
        "spn_69_two_speed_axle": two_speed_axle,
        "spn_70_parking_brake": parking_brake,
        "spn_1633_cruise_pause": cruise_pause,
        "spn_3807_park_brake_inhibit": park_brake_inhibit,
        "spn_595_cruise_active": cruise_active,
        "spn_596_cruise_enable": cruise_enable,
        "spn_597_brake_switch": brake_switch,
        "spn_598_clutch_switch": clutch_switch,
        "spn_976_pto_state": data[6] & 0x1F,
        "spn_527_cruise_state": (data[6] >> 5) & 0x07,
    }


# --------------------------------------------------------------------------- #
# Frame nesnesi
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class J1939Frame:
    """Tek bir J1939 CAN cercevesi (29-bit ID + 8 byte veri)."""

    can_id: int
    data: bytes
    pgn: int = PGN_CCVS1
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
        """candump/cansend bicimi: 18FEF100#00A03C0000FFFFFF"""
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
            "priority": self.priority,
            "source_address": self.source_address,
            "dlc": len(self.data),
            "timestamp": self.timestamp,
            **self.meta,
        }


def build_ccvs1_frame(
    speed_kmh: float | None,
    source_address: int,
    *,
    priority: int = DEFAULT_PRIORITY,
    timestamp: float = 0.0,
    **kwargs,
) -> J1939Frame:
    """Verilen hiz ve kaynak adres icin tam bir CCVS1 cercevesi uretir."""
    can_id = build_can_id(PGN_CCVS1, source_address=source_address, priority=priority)
    data = build_ccvs1_data(speed_kmh, **kwargs)
    return J1939Frame(
        can_id=can_id,
        data=data,
        pgn=PGN_CCVS1,
        priority=priority,
        source_address=source_address,
        timestamp=timestamp,
    )
