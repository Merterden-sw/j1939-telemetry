"""
J1939 protokol katmani.

    core     : 29-bit tanimlayici, bit paketleme, SPN olcekleme, cerceve nesnesi
    messages : PGN bazli mesaj tanimlari ve kayit defteri
"""

from .core import (
    BIT2_ERROR,
    BIT2_NOT_AVAILABLE,
    BIT2_OFF,
    BIT2_ON,
    BYTE_NOT_AVAILABLE,
    DEFAULT_PRIORITY,
    GLOBAL_ADDRESS,
    PDU1_MAX_PF,
    WORD_NOT_AVAILABLE,
    J1939Error,
    J1939Frame,
    build_can_id,
    decode_can_id,
    decode_scaled,
    encode_scaled,
    format_can_id,
    pack_2bit,
    unpack_2bit,
)
from .messages import (
    DLC,
    FAULT_POOL,
    FMI_NAMES,
    GEAR_OFFSET,
    MESSAGES,
    PERCENT_RESOLUTION,
    PGN_AMB,
    PGN_CCSS,
    PGN_CCVS1,
    PGN_DD,
    PGN_DM1,
    PGN_EBC1,
    PGN_EBC2,
    PGN_EEC1,
    PGN_EEC2,
    PGN_EFLP1,
    PGN_ET1,
    PGN_ETC1,
    PGN_ETC2,
    PGN_HOURS,
    PGN_HVBATT,
    PGN_IC1,
    PGN_LFE1,
    PGN_VDHR,
    PGN_VEP1,
    SPN84_MAX_KMH,
    SPN84_RESOLUTION,
    SPN_NAMES,
    MessageDef,
    build_amb,
    build_ccss,
    build_ccvs1,
    build_ccvs1_frame,
    build_dd,
    build_dm1,
    build_ebc1,
    build_ebc2,
    build_eec1,
    build_eec2,
    build_eflp1,
    build_et1,
    build_etc1,
    build_etc2,
    build_frame,
    build_hours,
    build_hvbatt,
    build_ic1,
    build_lfe1,
    build_vdhr,
    build_vep1,
    decode_gear,
    decode_percent,
    decode_range,
    decode_rel_speed,
    decode_rpm,
    decode_temp_byte,
    decode_temp_word,
    decode_torque_pct,
    encode_gear,
    encode_percent,
    encode_range,
    encode_rel_speed,
    encode_rpm,
    encode_temp_byte,
    encode_temp_word,
    encode_torque_pct,
    parse_amb,
    parse_ccss,
    parse_ccvs1,
    parse_dd,
    parse_dm1,
    parse_ebc1,
    parse_ebc2,
    parse_eec1,
    parse_eec2,
    parse_eflp1,
    parse_et1,
    parse_etc1,
    parse_etc2,
    parse_frame,
    parse_hours,
    parse_hvbatt,
    parse_ic1,
    parse_lfe1,
    parse_vdhr,
    parse_vep1,
)

# Geriye donuk adlar (eski j1939.py API'si)
CCVS1_DLC = DLC


def encode_wheel_speed(speed_kmh: float | None) -> int:
    """SPN 84: km/h -> ham deger (1/256 km/h per bit)."""
    return encode_scaled(speed_kmh, resolution=SPN84_RESOLUTION, byte_length=2)


def decode_wheel_speed(raw: int) -> float | None:
    """SPN 84: ham deger -> km/h."""
    return decode_scaled(raw, resolution=SPN84_RESOLUTION, byte_length=2)


def wheel_speed_bytes(speed_kmh: float | None) -> tuple[int, int]:
    """SPN 84 icin (dusuk byte, yuksek byte) - little-endian."""
    raw = encode_wheel_speed(speed_kmh)
    return raw & 0xFF, (raw >> 8) & 0xFF
