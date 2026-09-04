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
    PGN 65226  0xFECA  DM1     Active Diagnostic Trouble Codes (basitlestirilmis)
    PGN 65267  0xFEF3  VEP1    Vehicle Position (Latitude/Longitude)
    PGN 65253  0xFEE5  HOURS   Engine Hours, Revolutions
    PGN 65248  0xFEE0  VDHR    High Resolution Vehicle Distance
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
PGN_DM1: Final[int] = 65226  # 0xFECA
PGN_VEP1: Final[int] = 65267  # 0xFEF3
PGN_HOURS: Final[int] = 65253  # 0xFEE5
PGN_VDHR: Final[int] = 65248  # 0xFEE0

DLC: Final[int] = 8

# SPN 584/585 - Latitude / Longitude: 1e-7 deg/bit, offset -210
POSITION_RESOLUTION: Final[float] = 1e-7
POSITION_OFFSET: Final[float] = -210.0

# SPN 247 - Engine Total Hours of Operation: 0.05 h/bit
ENGINE_HOURS_RESOLUTION: Final[float] = 0.05

# SPN 917 (Trip) / 918 (Total) - High Resolution Vehicle Distance: 5 m/bit
DISTANCE_RESOLUTION: Final[float] = 0.005

# DM1 - basitlestirilmis tek-DTC gosterimi (bkz. build_dm1 docstring)
DM1_FMI_NOT_AVAILABLE: Final[int] = 0x1F

# Sik kullanilan SPN adlari (arac ariza panelinde gosterim icin)
SPN_NAMES: Final[dict[int, str]] = {
    70: "Park Freni Anahtari",
    84: "Tekerlek Hizi",
    91: "Gaz Pedali Pozisyonu",
    94: "Yakit Filtresi Basinci",
    97: "Su/Yakit Ayirici Seviyesi",
    100: "Motor Yag Basinci",
    105: "Turbo Emme Havasi Sicakligi",
    110: "Motor Sogutma Suyu Sicakligi",
    111: "Sogutma Suyu Seviyesi",
    158: "Anahtarli Batarya Voltaji",
    168: "Batarya Sarj Voltaji",
    174: "Yakit Sicakligi",
    190: "Motor Devri (RPM)",
    247: "Motor Calisma Saati",
    629: "ECU Dahili Ariza",
    639: "CAN Veri Yolu Hatasi",
}

# Sik kullanilan FMI (Failure Mode Identifier) aciklamalari (J1939-73)
FMI_NAMES: Final[dict[int, str]] = {
    0: "Deger Cok Yuksek (Kritik)",
    1: "Deger Cok Dusuk (Kritik)",
    2: "Veri Guvenilmez / Kararsiz",
    3: "Voltaj Yuksek / Kisa Devre",
    4: "Voltaj Dusuk / Topraklama",
    5: "Akim Dusuk / Devre Acik",
    6: "Akim Yuksek / Kisa Devre",
    7: "Mekanik Ariza",
    9: "Anormal Guncelleme Hizi",
    11: "Kok Neden Bilinmiyor",
    12: "Cihaz Arizali",
    13: "Kalibrasyon Disi",
    14: "Ozel Talimat",
    16: "Deger Cok Yuksek (Orta)",
    17: "Deger Cok Dusuk (Orta)",
    18: "Deger Cok Yuksek (Az Onemli)",
    19: "Sebeke Veri Hatasi",
    31: "Durum Mevcut / Onaylanmis",
}

# Rastgele ariza tetiklemesi icin ornek SPN/FMI havuzu (SPN_NAMES alt kumesi)
FAULT_POOL: Final[tuple[tuple[int, int], ...]] = (
    (110, 0),
    (110, 16),
    (100, 1),
    (100, 17),
    (168, 4),
    (168, 3),
    (97, 31),
    (105, 0),
    (111, 17),
    (639, 2),
)

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
# PGN 65226 - DM1 (Active Diagnostic Trouble Codes)
# --------------------------------------------------------------------------- #
#
# Gercek J1939-73 DM1 mesaji, birden fazla DTC oldugunda 8 byte'i asar ve
# TP.BAM (multi-packet transport) gerektirir. Bu simulator TP katmanini
# uygulamiyor; bunun yerine DM1'i her zaman sabit 8 byte'lik, EN FAZLA BIR
# aktif DTC gosteren basitlestirilmis bir cerceve olarak kodluyor. Aracin TAM
# ariza listesi VehicleState.active_dtcs uzerinden JSON/WS ile ayrica tasinir;
# CAN cercevesi yalnizca "en son" DTC'yi (veya hic yoksa temiz durumu) temsil
# eder.
#
#     Byte 1 : Lamp status  (bit0-1 MIL, digerleri not-available)
#     Byte 2 : Flash status (0xFF = not available)
#     Byte 3 : SPN dusuk byte (bit 0-7)
#     Byte 4 : SPN orta byte  (bit 8-15)
#     Byte 5 : bit 0-2 SPN yuksek 3 bit, bit 3-7 FMI (5 bit)
#     Byte 6 : bit 0-6 Occurrence Count, bit 7 SPN Conversion Method
#     Byte 7-8: rezerve (0xFF)


def build_dm1(signals: dict) -> bytes:
    spn = signals.get("dtc_spn")
    fmi = signals.get("dtc_fmi")
    has_fault = spn is not None and fmi is not None
    occurrence_count = signals.get("dtc_occurrence_count", 1)

    byte1 = 0b01 if has_fault else 0b00  # MIL: 01=on, 00=off
    byte2 = BYTE_NOT_AVAILABLE

    if has_fault:
        if not 0 <= spn <= 0x7FFFF:
            raise J1939Error(f"SPN 0-524287 araliginda olmali: {spn}")
        if not 0 <= fmi <= 0x1F:
            raise J1939Error(f"FMI 0-31 araliginda olmali: {fmi}")
        byte3 = spn & 0xFF
        byte4 = (spn >> 8) & 0xFF
        byte5 = ((spn >> 16) & 0x07) | ((fmi & 0x1F) << 3)
        byte6 = (min(max(int(occurrence_count), 0), 0x7F)) | 0x80
    else:
        byte3 = 0x00
        byte4 = 0x00
        byte5 = DM1_FMI_NOT_AVAILABLE << 3
        byte6 = 0x00

    return bytes((byte1, byte2, byte3, byte4, byte5, byte6, 0xFF, 0xFF))


def parse_dm1(data: bytes) -> dict:
    mil = data[0] & 0x03
    spn = data[2] | (data[3] << 8) | ((data[4] & 0x07) << 16)
    fmi = (data[4] >> 3) & 0x1F
    occurrence_count = data[5] & 0x7F
    active = mil == 0b01 and fmi != DM1_FMI_NOT_AVAILABLE

    return {
        "mil_lamp_on": mil == 0b01,
        "spn": spn if active else None,
        "spn_name": SPN_NAMES.get(spn) if active else None,
        "fmi": fmi if active else None,
        "fmi_name": FMI_NAMES.get(fmi) if active else None,
        "occurrence_count": occurrence_count if active else 0,
    }


# --------------------------------------------------------------------------- #
# 4-byte (dogrudan) olcekleme - core.encode_scaled/decode_scaled 1-2 byte'lik
# alanlar icin sentinel degerler kullanir; 32-bit SPN'ler (konum/saat/mesafe)
# icin ayni matematigi burada tekrarliyoruz.
# --------------------------------------------------------------------------- #

DWORD_NOT_AVAILABLE: Final[int] = 0xFFFFFFFF
DWORD_ERROR: Final[int] = 0xFE000000


def _encode_scaled32(value: float | None, *, resolution: float, offset: float = 0.0) -> int:
    if value is None:
        return DWORD_NOT_AVAILABLE
    raw = int(round((float(value) - offset) / resolution))
    return min(max(raw, 0), DWORD_NOT_AVAILABLE - 1)


def _decode_scaled32(
    raw: int, *, resolution: float, offset: float = 0.0, digits: int = 3
) -> float | None:
    if raw >= DWORD_ERROR:
        return None
    return round(raw * resolution + offset, digits)


# --------------------------------------------------------------------------- #
# PGN 65267 - Vehicle Position (SPN 584 Latitude / SPN 585 Longitude)
# --------------------------------------------------------------------------- #


def build_vep1(signals: dict) -> bytes:
    lat_raw = _encode_scaled32(
        signals.get("latitude_deg"), resolution=POSITION_RESOLUTION, offset=POSITION_OFFSET
    )
    lon_raw = _encode_scaled32(
        signals.get("longitude_deg"), resolution=POSITION_RESOLUTION, offset=POSITION_OFFSET
    )
    return lat_raw.to_bytes(4, "little") + lon_raw.to_bytes(4, "little")


def parse_vep1(data: bytes) -> dict:
    lat_raw = int.from_bytes(data[0:4], "little")
    lon_raw = int.from_bytes(data[4:8], "little")
    return {
        "spn_584_latitude_deg": _decode_scaled32(
            lat_raw, resolution=POSITION_RESOLUTION, offset=POSITION_OFFSET, digits=7
        ),
        "spn_585_longitude_deg": _decode_scaled32(
            lon_raw, resolution=POSITION_RESOLUTION, offset=POSITION_OFFSET, digits=7
        ),
    }


# --------------------------------------------------------------------------- #
# PGN 65253 - Engine Hours, Revolutions (SPN 247)
# --------------------------------------------------------------------------- #


def build_hours(signals: dict) -> bytes:
    raw = _encode_scaled32(signals.get("engine_hours"), resolution=ENGINE_HOURS_RESOLUTION)
    return raw.to_bytes(4, "little") + b"\xff\xff\xff\xff"


def parse_hours(data: bytes) -> dict:
    raw = int.from_bytes(data[0:4], "little")
    return {
        "spn_247_engine_hours": _decode_scaled32(raw, resolution=ENGINE_HOURS_RESOLUTION, digits=2)
    }


# --------------------------------------------------------------------------- #
# PGN 65248 - High Resolution Vehicle Distance (SPN 917 Trip / SPN 918 Total)
# --------------------------------------------------------------------------- #


def build_vdhr(signals: dict) -> bytes:
    trip_raw = _encode_scaled32(signals.get("trip_km"), resolution=DISTANCE_RESOLUTION)
    total_raw = _encode_scaled32(signals.get("total_km"), resolution=DISTANCE_RESOLUTION)
    return trip_raw.to_bytes(4, "little") + total_raw.to_bytes(4, "little")


def parse_vdhr(data: bytes) -> dict:
    trip_raw = int.from_bytes(data[0:4], "little")
    total_raw = int.from_bytes(data[4:8], "little")
    return {
        "spn_917_trip_distance_km": _decode_scaled32(
            trip_raw, resolution=DISTANCE_RESOLUTION, digits=3
        ),
        "spn_918_total_distance_km": _decode_scaled32(
            total_raw, resolution=DISTANCE_RESOLUTION, digits=3
        ),
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
    PGN_DM1: MessageDef(
        pgn=PGN_DM1,
        acronym="DM1",
        name="Active Diagnostic Trouble Codes",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_dm1,
        parser=parse_dm1,
        spns=("1213 MIL Lamp", "1214 SPN", "1215 FMI", "1216 Occurrence Count"),
    ),
    PGN_VEP1: MessageDef(
        pgn=PGN_VEP1,
        acronym="VEP1",
        name="Vehicle Position",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_vep1,
        parser=parse_vep1,
        spns=("584 Latitude", "585 Longitude"),
    ),
    PGN_HOURS: MessageDef(
        pgn=PGN_HOURS,
        acronym="HOURS",
        name="Engine Hours, Revolutions",
        priority=6,
        transmit_rate_ms=5000,
        builder=build_hours,
        parser=parse_hours,
        spns=("247 Engine Total Hours of Operation",),
    ),
    PGN_VDHR: MessageDef(
        pgn=PGN_VDHR,
        acronym="VDHR",
        name="High Resolution Vehicle Distance",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_vdhr,
        parser=parse_vdhr,
        spns=(
            "917 Trip Distance (High Resolution)",
            "918 Total Vehicle Distance (High Resolution)",
        ),
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
