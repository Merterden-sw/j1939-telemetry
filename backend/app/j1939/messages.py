"""
J1939 mesaj tanimlari (J1939-71 uygulama katmani).

Her mesaj icin bir MessageDef kaydi tutulur: PGN, oncelik, veri uzunlugu,
yayin periyodu ve veri alanini kuran/cozen fonksiyonlar. Simulator bu kayit
defteri uzerinden gezerek her arac icin ilgili cerceveleri uretir.

Desteklenen mesajlar:

    PGN 65265  0xFEF1  CCVS1   Cruise Control / Vehicle Speed 1
    PGN 61443  0xF003  EEC2    Electronic Engine Controller 2
    PGN 61445  0xF005  ETC2    Electronic Transmission Controller 2
    PGN 61441  0xF001  EBC1    Electronic Brake Controller 1
    PGN 64923  0xFD9B  HVBATT  Yuksek gerilim batarya paketi
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from .core import (
    BIT2_NOT_AVAILABLE,
    BIT2_OFF,
    BYTE_NOT_AVAILABLE,
    J1939Error,
    J1939Frame,
    build_can_id,
    decode_scaled,
    encode_scaled,
    pack_2bit,
    unpack_2bit,
)

# --------------------------------------------------------------------------- #
# PGN numaralari
# --------------------------------------------------------------------------- #

PGN_CCVS1: Final[int] = 65265  # 0xFEF1
PGN_EEC2: Final[int] = 61443  # 0xF003
PGN_ETC2: Final[int] = 61445  # 0xF005
PGN_EBC1: Final[int] = 61441  # 0xF001
PGN_HVBATT: Final[int] = 64923  # 0xFD9B

DLC: Final[int] = 8

# --------------------------------------------------------------------------- #
# SPN olcek tanimlari
# --------------------------------------------------------------------------- #

# SPN 84 - Wheel-Based Vehicle Speed: 1/256 km/h, 0 .. 250.996
SPN84_RESOLUTION: Final[float] = 1.0 / 256.0
SPN84_MAX_KMH: Final[float] = 0xFAFF * SPN84_RESOLUTION

# SPN 91 / 29 / 974 (pedal), 521 (fren pedali), 5464 / 5465 (batarya): 0.4 %/bit
PERCENT_RESOLUTION: Final[float] = 0.4
PERCENT_RAW_MAX: Final[int] = 250  # 250 * 0.4 = %100

# SPN 92 - Engine Percent Load: 1 %/bit, 0 .. 125
LOAD_RESOLUTION: Final[float] = 1.0

# SPN 523 / 524 - vites: 1 vites/bit, offset -125
GEAR_OFFSET: Final[int] = -125
GEAR_RAW_NEUTRAL: Final[int] = 125

# SPN 526 - Transmission Actual Gear Ratio: 0.001/bit, 0 .. 64.255
GEAR_RATIO_RESOLUTION: Final[float] = 0.001

# SPN 162 / 163 - Transmission Range: iki ASCII karakter
RANGE_LABELS: Final[tuple[str, ...]] = ("P", "R", "N", "D")
RANGE_NOT_AVAILABLE: Final[bytes] = b"\xff\xff"


# --------------------------------------------------------------------------- #
# Yardimcilar
# --------------------------------------------------------------------------- #


def encode_percent(value: float | None) -> int:
    """Yuzde degerini 0.4 %/bit cozunurlukle tek byte'a cevirir."""
    return encode_scaled(
        value, resolution=PERCENT_RESOLUTION, byte_length=1, max_valid=PERCENT_RAW_MAX
    )


def decode_percent(raw: int) -> float | None:
    """0.4 %/bit ham degeri yuzdeye cevirir."""
    return decode_scaled(raw, resolution=PERCENT_RESOLUTION, byte_length=1, digits=1)


def encode_gear(gear: int | None) -> int:
    """
    Vites numarasini SPN 523/524 ham degerine cevirir (offset -125).

        ileri vites n  ->  125 + n
        bos (neutral)  ->  125
        geri vites -n  ->  125 - n
    """
    if gear is None:
        return BYTE_NOT_AVAILABLE
    raw = int(gear) - GEAR_OFFSET
    if not 0 <= raw <= 250:
        raise J1939Error(f"Vites araligi disi: {gear} (raw {raw})")
    return raw


def decode_gear(raw: int) -> int | None:
    """SPN 523/524 ham degerini vites numarasina cevirir."""
    if raw > 250:
        return None
    return raw + GEAR_OFFSET


def encode_range(label: str | None) -> bytes:
    """Vites kademesini (P/R/N/D) SPN 162/163 icin iki ASCII byte'a cevirir."""
    if not label:
        return RANGE_NOT_AVAILABLE
    text = label.upper()[:2].ljust(2)
    return text.encode("ascii")


def decode_range(data: bytes) -> str | None:
    """SPN 162/163 ASCII alanini cozer."""
    if data == RANGE_NOT_AVAILABLE:
        return None
    return data.decode("ascii", errors="replace").strip() or None


def _bit2(flag: bool | None) -> int:
    """Boolean degeri 2-bit J1939 durumuna cevirir."""
    if flag is None:
        return BIT2_NOT_AVAILABLE
    return 0b01 if flag else 0b00


# --------------------------------------------------------------------------- #
# PGN 65265 - CCVS1
# --------------------------------------------------------------------------- #


def build_ccvs1(signals: dict) -> bytes:
    """
    Byte 1 : SPN 69 / 70 / 1633 / 3807   (2'ser bit)
    Byte 2-3: SPN 84  Wheel-Based Vehicle Speed (1/256 km/h, little-endian)
    Byte 4 : SPN 595 / 596 / 597 / 598   (2'ser bit)
    Byte 5 : SPN 599 / 600 / 601 / 602   (2'ser bit)
    Byte 6 : SPN 86  Cruise Control Set Speed (1 km/h/bit)
    Byte 7 : SPN 976 PTO durumu (5 bit) + SPN 527 CC durumu (3 bit)
    Byte 8 : SPN 968 / 967 / 966 / 1237  (2'ser bit)

    Verilmeyen anahtar sinyalleri "kapali" (0b00) kabul edilir; simulator
    her tick'te bunlarin gercek degerini gonderir.
    """
    raw_speed = encode_scaled(signals.get("speed_kmh"), resolution=SPN84_RESOLUTION, byte_length=2)
    set_speed = signals.get("cruise_set_speed_kmh")
    byte6 = BYTE_NOT_AVAILABLE if set_speed is None else min(max(int(round(set_speed)), 0), 250)

    return bytes(
        (
            pack_2bit(
                BIT2_NOT_AVAILABLE,  # SPN 69  iki kademeli aks
                _bit2(signals.get("parking_brake", False)),  # SPN 70  el freni
                BIT2_NOT_AVAILABLE,  # SPN 1633 cruise pause
                BIT2_NOT_AVAILABLE,  # SPN 3807 park brake inhibit
            ),
            raw_speed & 0xFF,
            (raw_speed >> 8) & 0xFF,
            pack_2bit(
                _bit2(signals.get("cruise_active", False)),  # SPN 595
                _bit2(signals.get("cruise_active", False)),  # SPN 596
                _bit2(signals.get("brake_switch", False)),  # SPN 597 fren anahtari
                BIT2_OFF,  # SPN 598 debriyaj
            ),
            pack_2bit(BIT2_OFF, BIT2_OFF, BIT2_OFF, BIT2_OFF),
            byte6,
            0x1F,  # PTO kapali + CC durumu 0
            pack_2bit(*([BIT2_NOT_AVAILABLE] * 4)),
        )
    )


def parse_ccvs1(data: bytes) -> dict:
    axle, parking, pause, inhibit = unpack_2bit(data[0])
    cc_active, cc_enable, brake, clutch = unpack_2bit(data[3])
    return {
        "spn_84_wheel_based_speed_kmh": decode_scaled(
            data[1] | (data[2] << 8), resolution=SPN84_RESOLUTION, byte_length=2
        ),
        "spn_84_raw": data[1] | (data[2] << 8),
        "spn_70_parking_brake": parking,
        "spn_595_cruise_active": cc_active,
        "spn_596_cruise_enable": cc_enable,
        "spn_597_brake_switch": brake,
        "spn_598_clutch_switch": clutch,
        "spn_86_cruise_set_speed_kmh": None if data[5] == BYTE_NOT_AVAILABLE else data[5],
        "spn_976_pto_state": data[6] & 0x1F,
        "spn_527_cruise_state": (data[6] >> 5) & 0x07,
    }


# --------------------------------------------------------------------------- #
# PGN 61443 - EEC2
# --------------------------------------------------------------------------- #


def build_eec2(signals: dict) -> bytes:
    """
    Byte 1 : SPN 558 / 559 / 1437 / 2970  (2'ser bit)
    Byte 2 : SPN 91  Accelerator Pedal Position 1 (0.4 %/bit)
    Byte 3 : SPN 92  Engine Percent Load At Current Speed (1 %/bit)
    Byte 4 : SPN 974 Remote Accelerator Pedal Position (0.4 %/bit)
    Byte 5 : SPN 29  Accelerator Pedal Position 2 (0.4 %/bit)
    Byte 6-8: kullanilmiyor (not available)
    """
    accel = signals.get("accel_pedal_pct")
    low_idle = accel is not None and accel < 1.0

    return bytes(
        (
            pack_2bit(
                _bit2(low_idle),  # SPN 558  rolanti anahtari
                _bit2(signals.get("kickdown")),  # SPN 559  kickdown
                BIT2_NOT_AVAILABLE,  # SPN 1437 hiz limiti durumu
                BIT2_NOT_AVAILABLE,  # SPN 2970 pedal 2 rolanti
            ),
            encode_percent(accel),
            encode_scaled(
                signals.get("engine_load_pct"), resolution=LOAD_RESOLUTION, byte_length=1
            ),
            encode_percent(signals.get("remote_accel_pct")),
            encode_percent(signals.get("accel_pedal2_pct")),
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_eec2(data: bytes) -> dict:
    low_idle, kickdown, speed_limit, idle2 = unpack_2bit(data[0])
    return {
        "spn_91_accelerator_pedal_position_1_pct": decode_percent(data[1]),
        "spn_91_raw": data[1],
        "spn_92_engine_percent_load_pct": decode_scaled(
            data[2], resolution=LOAD_RESOLUTION, byte_length=1, digits=0
        ),
        "spn_974_remote_accelerator_pct": decode_percent(data[3]),
        "spn_29_accelerator_pedal_position_2_pct": decode_percent(data[4]),
        "spn_558_low_idle_switch": low_idle,
        "spn_559_kickdown_switch": kickdown,
    }


# --------------------------------------------------------------------------- #
# PGN 61445 - ETC2
# --------------------------------------------------------------------------- #


def build_etc2(signals: dict) -> bytes:
    """
    Byte 1 : SPN 524 Transmission Selected Gear   (1 vites/bit, offset -125)
    Byte 2-3: SPN 526 Transmission Actual Gear Ratio (0.001/bit, little-endian)
    Byte 4 : SPN 523 Transmission Current Gear    (1 vites/bit, offset -125)
    Byte 5-6: SPN 162 Transmission Requested Range (2 ASCII karakter)
    Byte 7-8: SPN 163 Transmission Current Range   (2 ASCII karakter)
    """
    ratio = encode_scaled(
        signals.get("gear_ratio"), resolution=GEAR_RATIO_RESOLUTION, byte_length=2
    )
    return bytes(
        (
            encode_gear(signals.get("selected_gear")),
            ratio & 0xFF,
            (ratio >> 8) & 0xFF,
            encode_gear(signals.get("current_gear")),
            *encode_range(signals.get("requested_range")),
            *encode_range(signals.get("current_range")),
        )
    )


def parse_etc2(data: bytes) -> dict:
    return {
        "spn_524_selected_gear": decode_gear(data[0]),
        "spn_526_actual_gear_ratio": decode_scaled(
            data[1] | (data[2] << 8), resolution=GEAR_RATIO_RESOLUTION, byte_length=2
        ),
        "spn_523_current_gear": decode_gear(data[3]),
        "spn_523_raw": data[3],
        "spn_162_requested_range": decode_range(data[4:6]),
        "spn_163_current_range": decode_range(data[6:8]),
    }


# --------------------------------------------------------------------------- #
# PGN 61441 - EBC1
# --------------------------------------------------------------------------- #


def build_ebc1(signals: dict) -> bytes:
    """
    Byte 1 : SPN 561 / 562 / 563 / 1121  (2'ser bit)
    Byte 2 : SPN 521 Brake Pedal Position (0.4 %/bit)
    Byte 3 : SPN 575 / 576 / 577 / 1238  (2'ser bit)
    Byte 4-8: kullanilmiyor (not available)
    """
    return bytes(
        (
            pack_2bit(
                _bit2(signals.get("asr_engine_control")),  # SPN 561
                _bit2(signals.get("asr_brake_control")),  # SPN 562
                _bit2(signals.get("abs_active")),  # SPN 563
                _bit2(signals.get("ebs_brake_switch")),  # SPN 1121
            ),
            encode_percent(signals.get("brake_pedal_pct")),
            pack_2bit(*([BIT2_NOT_AVAILABLE] * 4)),
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_ebc1(data: bytes) -> dict:
    asr_engine, asr_brake, abs_active, ebs_switch = unpack_2bit(data[0])
    return {
        "spn_521_brake_pedal_position_pct": decode_percent(data[1]),
        "spn_521_raw": data[1],
        "spn_561_asr_engine_control": asr_engine,
        "spn_562_asr_brake_control": asr_brake,
        "spn_563_abs_active": abs_active,
        "spn_1121_ebs_brake_switch": ebs_switch,
    }


# --------------------------------------------------------------------------- #
# PGN 64923 - Yuksek gerilim batarya paketi
# --------------------------------------------------------------------------- #


def build_hvbatt(signals: dict) -> bytes:
    """
    Byte 1 : SPN 5464 State of Charge  (0.4 %/bit)
    Byte 2 : SPN 5465 State of Health  (0.4 %/bit)
    Byte 3-8: kullanilmiyor (not available)

    Not: Bu PGN'in 3-8. byte yerlesimini standarttan dogrulayamadigimiz icin
    yalnizca tanimli iki SPN yayinlanir; kalan byte'lar 0xFF (veri yok) olarak
    birakilir. Uydurma sinyal eklenmez.
    """
    return bytes(
        (
            encode_percent(signals.get("soc_pct")),
            encode_percent(signals.get("soh_pct")),
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_hvbatt(data: bytes) -> dict:
    return {
        "spn_5464_state_of_charge_pct": decode_percent(data[0]),
        "spn_5464_raw": data[0],
        "spn_5465_state_of_health_pct": decode_percent(data[1]),
        "spn_5465_raw": data[1],
    }


# --------------------------------------------------------------------------- #
# Kayit defteri
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MessageDef:
    """Tek bir J1939 mesajinin tanimi."""

    pgn: int
    acronym: str
    name: str
    priority: int
    transmit_rate_ms: int
    builder: Callable[[dict], bytes]
    parser: Callable[[bytes], dict]
    spns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pgn_hex(self) -> str:
        return f"0x{self.pgn:04X}"

    def to_dict(self) -> dict:
        return {
            "pgn": self.pgn,
            "pgn_hex": self.pgn_hex,
            "acronym": self.acronym,
            "name": self.name,
            "priority": self.priority,
            "transmit_rate_ms": self.transmit_rate_ms,
            "dlc": DLC,
            "spns": list(self.spns),
        }


MESSAGES: Final[dict[int, MessageDef]] = {
    PGN_CCVS1: MessageDef(
        pgn=PGN_CCVS1,
        acronym="CCVS1",
        name="Cruise Control / Vehicle Speed 1",
        priority=6,
        transmit_rate_ms=100,
        builder=build_ccvs1,
        parser=parse_ccvs1,
        spns=(
            "84 Wheel-Based Vehicle Speed",
            "86 Cruise Set Speed",
            "597 Brake Switch",
            "70 Parking Brake",
        ),
    ),
    PGN_EEC2: MessageDef(
        pgn=PGN_EEC2,
        acronym="EEC2",
        name="Electronic Engine Controller 2",
        priority=3,
        transmit_rate_ms=50,
        builder=build_eec2,
        parser=parse_eec2,
        spns=(
            "91 Accelerator Pedal Position 1",
            "92 Engine Percent Load",
            "974 Remote Accelerator",
            "29 Accelerator Pedal Position 2",
        ),
    ),
    PGN_ETC2: MessageDef(
        pgn=PGN_ETC2,
        acronym="ETC2",
        name="Electronic Transmission Controller 2",
        priority=3,
        transmit_rate_ms=100,
        builder=build_etc2,
        parser=parse_etc2,
        spns=(
            "523 Current Gear",
            "524 Selected Gear",
            "526 Actual Gear Ratio",
            "162/163 Transmission Range",
        ),
    ),
    PGN_EBC1: MessageDef(
        pgn=PGN_EBC1,
        acronym="EBC1",
        name="Electronic Brake Controller 1",
        priority=6,
        transmit_rate_ms=100,
        builder=build_ebc1,
        parser=parse_ebc1,
        spns=("521 Brake Pedal Position", "563 ABS Active", "1121 EBS Brake Switch"),
    ),
    PGN_HVBATT: MessageDef(
        pgn=PGN_HVBATT,
        acronym="HVBATT",
        name="Yuksek gerilim batarya paketi",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_hvbatt,
        parser=parse_hvbatt,
        spns=("5464 State of Charge", "5465 State of Health"),
    ),
}


def build_frame(
    pgn: int,
    signals: dict,
    source_address: int,
    *,
    timestamp: float = 0.0,
    priority: int | None = None,
) -> J1939Frame:
    """Kayit defterindeki tanima gore tam bir J1939 cercevesi uretir."""
    message = MESSAGES.get(pgn)
    if message is None:
        raise J1939Error(f"Tanimsiz PGN: {pgn}")

    prio = message.priority if priority is None else priority
    data = message.builder(signals)
    if len(data) != DLC:
        raise J1939Error(f"{message.acronym} veri alani {DLC} byte olmali: {len(data)}")

    return J1939Frame(
        can_id=build_can_id(pgn, source_address=source_address, priority=prio),
        data=data,
        pgn=pgn,
        acronym=message.acronym,
        priority=prio,
        source_address=source_address,
        timestamp=timestamp,
    )


def parse_frame(pgn: int, data: bytes) -> dict:
    """Veri alanini ilgili PGN tanimina gore cozumler."""
    message = MESSAGES.get(pgn)
    if message is None:
        raise J1939Error(f"Tanimsiz PGN: {pgn}")
    if len(data) != DLC:
        raise J1939Error(f"{message.acronym} veri alani {DLC} byte olmali: {len(data)}")
    return message.parser(data)


# Kisayol: en sik kullanilan CCVS1 cercevesi
def build_ccvs1_frame(
    speed_kmh: float | None,
    source_address: int,
    *,
    priority: int | None = None,
    timestamp: float = 0.0,
    **signals,
) -> J1939Frame:
    """Hiz ve istege bagli anahtar durumlariyla CCVS1 cercevesi uretir."""
    return build_frame(
        PGN_CCVS1,
        {"speed_kmh": speed_kmh, **signals},
        source_address,
        timestamp=timestamp,
        priority=priority,
    )
