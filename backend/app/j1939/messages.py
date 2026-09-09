"""
J1939 mesaj tanimlari (J1939-71 uygulama katmani).

Her mesaj icin bir MessageDef kaydi tutulur: PGN, oncelik, veri uzunlugu,
yayin periyodu ve veri alanini kuran/cozen fonksiyonlar. Simulator bu kayit
defteri uzerinden gezerek her arac icin ilgili cerceveleri uretir.

Desteklenen mesajlar:

    PGN 61444  0xF004  EEC1    Electronic Engine Controller 1
    PGN 61443  0xF003  EEC2    Electronic Engine Controller 2
    PGN 61442  0xF002  ETC1    Electronic Transmission Controller 1
    PGN 61445  0xF005  ETC2    Electronic Transmission Controller 2
    PGN 61441  0xF001  EBC1    Electronic Brake Controller 1
    PGN 65215  0xFEBF  EBC2    Electronic Brake Controller 2 (aks hizlari)
    PGN 65265  0xFEF1  CCVS1   Cruise Control / Vehicle Speed 1
    PGN 65096  0xFE48  CCSS    Cruise Control / Vehicle Speed Setup
    PGN 65266  0xFEF2  LFE1    Engine Fluid Level / Fuel Economy
    PGN 65262  0xFEEE  ET1     Engine Temperature 1
    PGN 65263  0xFEEF  EFLP1   Engine Fluid Level / Pressure 1
    PGN 65270  0xFEF6  IC1     Inlet / Exhaust Conditions 1
    PGN 65269  0xFEF5  AMB     Ambient Conditions
    PGN 65276  0xFEFC  DD      Dash Display
    PGN 64923  0xFD9B  HVBATT  Yuksek gerilim batarya paketi
    PGN 65226  0xFECA  DM1     Active Diagnostic Trouble Codes (basitlestirilmis)
    PGN 65267  0xFEF3  VEP1    Vehicle Position (Latitude/Longitude)
    PGN 65253  0xFEE5  HOURS   Engine Hours, Revolutions
    PGN 65248  0xFEE0  VDHR    High Resolution Vehicle Distance

Musteri DBC dosyasindan (J1939_CCVS1_ETC2_EBC1.dbc) alinan sinyaller bu kayit
defterine islenmistir. DBC ile SAE J1939-71 arasindaki farklarda standart esas
alinmistir; ayrintili gerekce icin bkz. README "DBC Uyum Notlari".

DBC'de yer alip yayinlanmayan sinyaller (standart bir yuvasi dogrulanamadi):

    SPN 352 / 353  AxleLocation / AxleWeight  - PGN 65258 (VW) alanidir, ayrica
                   DBC'deki 52|16 yerlesimi 8 baytlik cerceveye tasmaktadir.
    SPN 528 / 531  MomEngMaxPowerEnable / MomEngMaxOverspeedEnable
    SPN 619        ProgressiveShiftDisable
                   - DBC bunlari ETC2 bayt 6-7'ye koyar; o baytlar standartta
                     SPN 162/163 (vites kademesi, ASCII) tarafindan kullanilir.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from .core import (
    BIT2_NOT_AVAILABLE,
    BYTE_NOT_AVAILABLE,
    WORD_NOT_AVAILABLE,
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
PGN_EEC1: Final[int] = 61444  # 0xF004
PGN_EEC2: Final[int] = 61443  # 0xF003
PGN_ETC1: Final[int] = 61442  # 0xF002
PGN_ETC2: Final[int] = 61445  # 0xF005
PGN_EBC1: Final[int] = 61441  # 0xF001
PGN_EBC2: Final[int] = 65215  # 0xFEBF
PGN_CCSS: Final[int] = 65096  # 0xFE48
PGN_LFE1: Final[int] = 65266  # 0xFEF2
PGN_ET1: Final[int] = 65262  # 0xFEEE
PGN_EFLP1: Final[int] = 65263  # 0xFEEF
PGN_IC1: Final[int] = 65270  # 0xFEF6
PGN_AMB: Final[int] = 65269  # 0xFEF5
PGN_DD: Final[int] = 65276  # 0xFEFC
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
    51: "Gaz Kelebegi Pozisyonu",
    70: "Park Freni Anahtari",
    79: "Yol Yuzeyi Sicakligi",
    80: "Cam Suyu Seviyesi",
    81: "Partikul Filtresi Giris Basinci",
    84: "Tekerlek Hizi",
    86: "Cruise Ayar Hizi",
    91: "Gaz Pedali Pozisyonu",
    92: "Motor Yuku",
    94: "Yakit Filtresi Basinci",
    96: "Yakit Seviyesi 1",
    97: "Su/Yakit Ayirici Seviyesi",
    98: "Motor Yag Seviyesi",
    100: "Motor Yag Basinci",
    102: "Turbo Sarj (Boost) Basinci",
    105: "Turbo Emme Havasi Sicakligi",
    107: "Hava Filtresi Fark Basinci",
    108: "Barometrik Basinc",
    110: "Motor Sogutma Suyu Sicakligi",
    111: "Sogutma Suyu Seviyesi",
    112: "Sogutma Suyu Filtresi Fark Basinci",
    158: "Anahtarli Batarya Voltaji",
    161: "Sanziman Giris Mili Devri",
    168: "Batarya Sarj Voltaji",
    169: "Kargo Ortam Sicakligi",
    170: "Kabin Ici Sicakligi",
    171: "Dis Ortam Sicakligi",
    172: "Hava Giris Sicakligi",
    173: "Egzoz Gazi Sicakligi",
    174: "Yakit Sicakligi",
    175: "Motor Yag Sicakligi",
    176: "Turbo Yag Sicakligi",
    183: "Yakit Tuketim Hizi",
    184: "Anlik Yakit Ekonomisi",
    185: "Ortalama Yakit Ekonomisi",
    190: "Motor Devri (RPM)",
    191: "Sanziman Cikis Mili Devri",
    247: "Motor Calisma Saati",
    248: "PTO Calisma Saati",
    512: "Surucu Talebi Tork",
    513: "Gercek Motor Torku",
    521: "Fren Pedali Pozisyonu",
    522: "Debriyaj Kaymasi",
    523: "Guncel Vites",
    560: "Aktarma Organi Devrede",
    573: "Tork Konvertoru Kilidi",
    574: "Vites Degisimi Aktif",
    563: "ABS Aktif",
    899: "Motor Tork Modu",
    904: "On Aks Hizi",
    1420: "Bakim Uyari Lambasi",
    1675: "Mars Motoru Modu",
    1836: "Treyler Bagli",
    1856: "Emniyet Kemeri Anahtari",
    2432: "Talep Edilen Motor Torku",
    2911: "Toplam Fren Talebi",
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

# SPN 190 / 161 / 191 - devir: 0.125 rpm/bit, 2 byte
RPM_RESOLUTION: Final[float] = 0.125

# SPN 512 / 513 / 2432 - yuzde tork: 1 %/bit, offset -125 (-125 .. +125)
TORQUE_RESOLUTION: Final[float] = 1.0
TORQUE_OFFSET: Final[float] = -125.0

# SPN 110 / 174 / 105 / 172 - tek byte sicaklik: 1 C/bit, offset -40
TEMP_BYTE_RESOLUTION: Final[float] = 1.0
TEMP_BYTE_OFFSET: Final[float] = -40.0

# SPN 175 / 176 / 173 / 170 / 171 / 79 / 169 - iki byte sicaklik: 0.03125 C/bit, offset -273
TEMP_WORD_RESOLUTION: Final[float] = 0.03125
TEMP_WORD_OFFSET: Final[float] = -273.0

# SPN 100 / 94 - basinc: 4 kPa/bit  |  SPN 102 / 106: 2 kPa/bit
# SPN 108 / 81 / 112: 0.5 kPa/bit   |  SPN 107: 0.05 kPa/bit
PRESSURE_4KPA: Final[float] = 4.0
PRESSURE_2KPA: Final[float] = 2.0
PRESSURE_HALF_KPA: Final[float] = 0.5
PRESSURE_005KPA: Final[float] = 0.05

# SPN 183 - Engine Fuel Rate: 0.05 L/h per bit, 2 byte
FUEL_RATE_RESOLUTION: Final[float] = 0.05

# SPN 184 / 185 - yakit ekonomisi: 1/512 km/L per bit, 2 byte
FUEL_ECONOMY_RESOLUTION: Final[float] = 1.0 / 512.0

# SPN 905..910 - aks bazli bagil tekerlek hizi: 1/16 km/h per bit, offset -7.8125
REL_SPEED_RESOLUTION: Final[float] = 1.0 / 16.0
REL_SPEED_OFFSET: Final[float] = -7.8125

# Kaynak adres alani verilmediginde kullanilan bos deger (SPN 1481/1482/1483)
SOURCE_ADDRESS_NOT_AVAILABLE: Final[int] = BYTE_NOT_AVAILABLE


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


def _le(raw: int) -> tuple[int, int]:
    """16-bit ham degeri little-endian (dusuk, yuksek) bayt ciftine ayirir."""
    return raw & 0xFF, (raw >> 8) & 0xFF


def _word(data: bytes, index: int) -> int:
    """data[index] ve data[index+1] baytlarindan little-endian 16-bit deger okur."""
    return data[index] | (data[index + 1] << 8)


def encode_rpm(value: float | None) -> int:
    """SPN 190/161/191: devir -> 2 byte ham deger (0.125 rpm/bit)."""
    return encode_scaled(value, resolution=RPM_RESOLUTION, byte_length=2)


def decode_rpm(raw: int) -> float | None:
    """SPN 190/161/191: ham deger -> devir."""
    return decode_scaled(raw, resolution=RPM_RESOLUTION, byte_length=2, digits=1)


def encode_torque_pct(value: float | None) -> int:
    """SPN 512/513/2432: -125..+125 % tork -> tek byte (offset -125)."""
    return encode_scaled(value, resolution=TORQUE_RESOLUTION, offset=TORQUE_OFFSET, byte_length=1)


def decode_torque_pct(raw: int) -> float | None:
    """SPN 512/513/2432: ham deger -> yuzde tork."""
    return decode_scaled(
        raw, resolution=TORQUE_RESOLUTION, offset=TORQUE_OFFSET, byte_length=1, digits=0
    )


def encode_temp_byte(value: float | None) -> int:
    """SPN 110/174/105/172: -40..210 C -> tek byte (1 C/bit, offset -40)."""
    return encode_scaled(
        value, resolution=TEMP_BYTE_RESOLUTION, offset=TEMP_BYTE_OFFSET, byte_length=1
    )


def decode_temp_byte(raw: int) -> float | None:
    """SPN 110/174/105/172: ham deger -> santigrat."""
    return decode_scaled(
        raw, resolution=TEMP_BYTE_RESOLUTION, offset=TEMP_BYTE_OFFSET, byte_length=1, digits=0
    )


def encode_temp_word(value: float | None) -> int:
    """SPN 175/176/173/170/171/79/169: -273..1735 C -> 2 byte (0.03125 C/bit)."""
    return encode_scaled(
        value, resolution=TEMP_WORD_RESOLUTION, offset=TEMP_WORD_OFFSET, byte_length=2
    )


def decode_temp_word(raw: int) -> float | None:
    """SPN 175/176/173/170/171/79/169: ham deger -> santigrat."""
    return decode_scaled(
        raw, resolution=TEMP_WORD_RESOLUTION, offset=TEMP_WORD_OFFSET, byte_length=2, digits=2
    )


def encode_pressure(value: float | None, resolution: float) -> int:
    """Tek byte basinc alani (kPa); cozunurluk SPN'e gore degisir."""
    return encode_scaled(value, resolution=resolution, byte_length=1)


def decode_pressure(raw: int, resolution: float) -> float | None:
    """Tek byte basinc alanini kPa'ya cevirir."""
    return decode_scaled(raw, resolution=resolution, byte_length=1, digits=2)


def encode_rel_speed(value: float | None) -> int:
    """SPN 905-910: -7.8125..+7.8125 km/h bagil tekerlek hizi -> tek byte."""
    return encode_scaled(
        value, resolution=REL_SPEED_RESOLUTION, offset=REL_SPEED_OFFSET, byte_length=1
    )


def decode_rel_speed(raw: int) -> float | None:
    """SPN 905-910: ham deger -> bagil tekerlek hizi (km/h)."""
    return decode_scaled(
        raw, resolution=REL_SPEED_RESOLUTION, offset=REL_SPEED_OFFSET, byte_length=1, digits=4
    )


def _source_address(value: int | None) -> int:
    """SPN 1481/1482/1483: kontrol eden cihazin kaynak adresi."""
    if value is None:
        return SOURCE_ADDRESS_NOT_AVAILABLE
    if not 0 <= int(value) <= 0xFF:
        raise J1939Error(f"Kaynak adres 0-255 araliginda olmali: {value}")
    return int(value)


def _nibble(value: int | None, *, not_available: int = 0x0F) -> int:
    """4-bit ayrik parametre (SPN 899 / 1675 / 976 gibi)."""
    if value is None:
        return not_available
    if not 0 <= int(value) <= 0x0F:
        raise J1939Error(f"4-bit deger 0-15 araliginda olmali: {value}")
    return int(value)


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

    DBC'den gelen SPN 596/598/599/600/601/602/976 sinyalleri bayt 4-5-7'ye
    islenmistir. DBC'nin CCVS1'e koydugu SPN 1085/1086 (cruise ust/alt limit)
    standartta CCSS (PGN 65096) alanidir; bkz. build_ccss.
    """
    raw_speed = encode_scaled(signals.get("speed_kmh"), resolution=SPN84_RESOLUTION, byte_length=2)
    set_speed = signals.get("cruise_set_speed_kmh")
    byte6 = BYTE_NOT_AVAILABLE if set_speed is None else min(max(int(round(set_speed)), 0), 250)

    cruise_active = signals.get("cruise_active", False)
    # SPN 527 Cruise Control State: 0=kapali, 1=bekliyor, 4=hiz sabitleme aktif
    cruise_state = 4 if cruise_active else (1 if signals.get("cruise_enable") else 0)

    return bytes(
        (
            pack_2bit(
                BIT2_NOT_AVAILABLE,  # SPN 69  iki kademeli aks
                _bit2(signals.get("parking_brake", False)),  # SPN 70  el freni
                BIT2_NOT_AVAILABLE,  # SPN 1633 cruise pause
                BIT2_NOT_AVAILABLE,  # SPN 3807 park brake inhibit
            ),
            *_le(raw_speed),
            pack_2bit(
                _bit2(cruise_active),  # SPN 595
                _bit2(signals.get("cruise_enable", cruise_active)),  # SPN 596
                _bit2(signals.get("brake_switch", False)),  # SPN 597 fren anahtari
                _bit2(signals.get("clutch_switch", False)),  # SPN 598 debriyaj
            ),
            pack_2bit(
                _bit2(signals.get("cruise_set_switch", False)),  # SPN 599
                _bit2(signals.get("cruise_coast_switch", False)),  # SPN 600
                _bit2(signals.get("cruise_resume_switch", False)),  # SPN 601
                _bit2(signals.get("cruise_accel_switch", False)),  # SPN 602
            ),
            byte6,
            # Bayt 7: bit 0-4 SPN 976 PTO durumu, bit 5-7 SPN 527 cruise durumu
            (_nibble(signals.get("pto_state"), not_available=0x1F) & 0x1F)
            | ((cruise_state & 0x07) << 5),
            pack_2bit(*([BIT2_NOT_AVAILABLE] * 4)),
        )
    )


def parse_ccvs1(data: bytes) -> dict:
    axle, parking, pause, inhibit = unpack_2bit(data[0])
    cc_active, cc_enable, brake, clutch = unpack_2bit(data[3])
    cc_set, cc_coast, cc_resume, cc_accel = unpack_2bit(data[4])
    return {
        "spn_84_wheel_based_speed_kmh": decode_scaled(
            _word(data, 1), resolution=SPN84_RESOLUTION, byte_length=2
        ),
        "spn_84_raw": _word(data, 1),
        "spn_70_parking_brake": parking,
        "spn_595_cruise_active": cc_active,
        "spn_596_cruise_enable": cc_enable,
        "spn_597_brake_switch": brake,
        "spn_598_clutch_switch": clutch,
        "spn_599_cruise_set_switch": cc_set,
        "spn_600_cruise_coast_switch": cc_coast,
        "spn_601_cruise_resume_switch": cc_resume,
        "spn_602_cruise_accel_switch": cc_accel,
        "spn_86_cruise_set_speed_kmh": None if data[5] == BYTE_NOT_AVAILABLE else data[5],
        "spn_976_pto_state": data[6] & 0x1F,
        "spn_527_cruise_state": (data[6] >> 5) & 0x07,
    }


# --------------------------------------------------------------------------- #
# PGN 65096 - CCSS (Cruise Control / Vehicle Speed Setup)
# --------------------------------------------------------------------------- #


def build_ccss(signals: dict) -> bytes:
    """
    Byte 1 : SPN 1085 Cruise Control High Set Limit Speed (1 km/h/bit)
    Byte 2 : SPN 1086 Cruise Control Low Set Limit Speed  (1 km/h/bit)
    Byte 3 : SPN 1087 Maximum Vehicle Speed Limit         (1 km/h/bit)
    Byte 4-8: kullanilmiyor (not available)

    DBC bu iki sinyali (1085/1086) CCVS1'in 44. ve 52. bitlerine koyar; orasi
    standartta SPN 86'nin devami ve PTO/CC durum baytidir. Standart yerlesim
    esas alinip sinyaller kendi PGN'ine tasinmistir.
    """
    return bytes(
        (
            encode_scaled(signals.get("cruise_high_limit_kmh"), resolution=1.0, byte_length=1),
            encode_scaled(signals.get("cruise_low_limit_kmh"), resolution=1.0, byte_length=1),
            encode_scaled(signals.get("max_speed_limit_kmh"), resolution=1.0, byte_length=1),
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_ccss(data: bytes) -> dict:
    return {
        "spn_1085_cruise_high_limit_kmh": decode_scaled(
            data[0], resolution=1.0, byte_length=1, digits=0
        ),
        "spn_1086_cruise_low_limit_kmh": decode_scaled(
            data[1], resolution=1.0, byte_length=1, digits=0
        ),
        "spn_1087_max_vehicle_speed_limit_kmh": decode_scaled(
            data[2], resolution=1.0, byte_length=1, digits=0
        ),
    }


# --------------------------------------------------------------------------- #
# PGN 61444 - EEC1
# --------------------------------------------------------------------------- #


def build_eec1(signals: dict) -> bytes:
    """
    Byte 1 : bit 0-3 SPN 899 Engine Torque Mode, bit 4-7 SPN 4154 (kullanilmiyor)
    Byte 2 : SPN 512  Driver's Demand Engine Percent Torque (1 %/bit, offset -125)
    Byte 3 : SPN 513  Actual Engine Percent Torque          (1 %/bit, offset -125)
    Byte 4-5: SPN 190 Engine Speed (0.125 rpm/bit, little-endian)
    Byte 6 : SPN 1483 Source Address of Controlling Device for Engine Control
    Byte 7 : bit 0-3 SPN 1675 Engine Starter Mode, bit 4-7 rezerve
    Byte 8 : SPN 2432 Engine Demand Percent Torque (1 %/bit, offset -125)

    DBC SPN 2432'yi 52. bite (bayt 7'nin ust yarisi) koyar; standart yerlesim
    bayt 8 oldugu icin oraya yazilir.
    """
    return bytes(
        (
            _nibble(signals.get("torque_mode", 0)) | 0xF0,
            encode_torque_pct(signals.get("driver_demand_torque_pct")),
            encode_torque_pct(signals.get("actual_engine_torque_pct")),
            *_le(encode_rpm(signals.get("engine_rpm"))),
            _source_address(signals.get("engine_source_address")),
            _nibble(signals.get("starter_mode", 0)) | 0xF0,
            encode_torque_pct(signals.get("demand_engine_torque_pct")),
        )
    )


def parse_eec1(data: bytes) -> dict:
    return {
        "spn_899_engine_torque_mode": data[0] & 0x0F,
        "spn_512_driver_demand_torque_pct": decode_torque_pct(data[1]),
        "spn_513_actual_engine_torque_pct": decode_torque_pct(data[2]),
        "spn_190_engine_speed_rpm": decode_rpm(_word(data, 3)),
        "spn_190_raw": _word(data, 3),
        "spn_1483_engine_source_address": (None if data[5] == BYTE_NOT_AVAILABLE else data[5]),
        "spn_1675_starter_mode": data[6] & 0x0F,
        "spn_2432_demand_engine_torque_pct": decode_torque_pct(data[7]),
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
    Byte 6 : SPN 539  Actual Maximum Available Engine Percent Torque (1 %/bit)
    Byte 7 : SPN 1481 Estimated Engine Parasitic Losses Percent (1 %/bit)
    Byte 8 : kullanilmiyor (not available)

    DBC pedal 2 rolanti anahtarini 24. bite koyar; orasi standartta SPN 974'un
    baytidir. Anahtar, standarttaki yerine (bayt 1, bit 6-7 / SPN 2970) yazilir.
    """
    accel = signals.get("accel_pedal_pct")
    low_idle = accel is not None and accel < 1.0
    accel2 = signals.get("accel_pedal2_pct")
    low_idle2 = None if accel2 is None else accel2 < 1.0

    return bytes(
        (
            pack_2bit(
                _bit2(low_idle),  # SPN 558  rolanti anahtari
                _bit2(signals.get("kickdown")),  # SPN 559  kickdown
                BIT2_NOT_AVAILABLE,  # SPN 1437 hiz limiti durumu
                _bit2(low_idle2),  # SPN 2970 pedal 2 rolanti
            ),
            encode_percent(accel),
            encode_scaled(
                signals.get("engine_load_pct"), resolution=LOAD_RESOLUTION, byte_length=1
            ),
            encode_percent(signals.get("remote_accel_pct")),
            encode_percent(accel2),
            encode_scaled(
                signals.get("max_available_torque_pct"), resolution=LOAD_RESOLUTION, byte_length=1
            ),
            encode_scaled(
                signals.get("parasitic_losses_pct"), resolution=LOAD_RESOLUTION, byte_length=1
            ),
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
        "spn_539_max_available_torque_pct": decode_scaled(
            data[5], resolution=LOAD_RESOLUTION, byte_length=1, digits=0
        ),
        "spn_1481_parasitic_losses_pct": decode_scaled(
            data[6], resolution=LOAD_RESOLUTION, byte_length=1, digits=0
        ),
        "spn_558_low_idle_switch": low_idle,
        "spn_559_kickdown_switch": kickdown,
        "spn_2970_pedal2_low_idle_switch": idle2,
    }


# --------------------------------------------------------------------------- #
# PGN 61442 - ETC1
# --------------------------------------------------------------------------- #


def build_etc1(signals: dict) -> bytes:
    """
    Byte 1 : bit 0-1 SPN 560 Driveline Engaged, bit 2-3 SPN 573 Torque Converter
             Lockup, bit 4-5 SPN 574 Shift In Process, bit 6-7 SPN 2900 (NA)
    Byte 2-3: SPN 191 Transmission Output Shaft Speed (0.125 rpm/bit)
    Byte 4 : SPN 522 Percent Clutch Slip (0.4 %/bit)
    Byte 5-6: SPN 161 Transmission Input Shaft Speed (0.125 rpm/bit)
    Byte 7 : SPN 1482 Source Address of Controlling Device for Transmission
    Byte 8 : SPN 606 / 607 / 608 / 609 Transmission Mode 1-4 (2'ser bit)

    DBC bayt 1'de sirayi (shift, lockup, driveline) tersine cevirir ve mod
    sinyallerine 523/1875-1877 SPN'lerini verir; ikisi de standarda gore
    duzeltilmistir (SPN 523 zaten ETC2'deki guncel vitestir).
    """
    return bytes(
        (
            pack_2bit(
                _bit2(signals.get("driveline_engaged")),  # SPN 560
                _bit2(signals.get("torque_converter_lockup")),  # SPN 573
                _bit2(signals.get("shift_in_process")),  # SPN 574
                BIT2_NOT_AVAILABLE,  # SPN 2900
            ),
            *_le(encode_rpm(signals.get("output_shaft_rpm"))),
            encode_percent(signals.get("clutch_slip_pct")),
            *_le(encode_rpm(signals.get("input_shaft_rpm"))),
            _source_address(signals.get("transmission_source_address")),
            pack_2bit(
                _bit2(signals.get("transmission_mode1")),  # SPN 606
                _bit2(signals.get("transmission_mode2")),  # SPN 607
                _bit2(signals.get("transmission_mode3")),  # SPN 608
                _bit2(signals.get("transmission_mode4")),  # SPN 609
            ),
        )
    )


def parse_etc1(data: bytes) -> dict:
    driveline, lockup, shifting, _reserved = unpack_2bit(data[0])
    mode1, mode2, mode3, mode4 = unpack_2bit(data[7])
    return {
        "spn_560_driveline_engaged": driveline,
        "spn_573_torque_converter_lockup": lockup,
        "spn_574_shift_in_process": shifting,
        "spn_191_output_shaft_rpm": decode_rpm(_word(data, 1)),
        "spn_522_clutch_slip_pct": decode_percent(data[3]),
        "spn_161_input_shaft_rpm": decode_rpm(_word(data, 4)),
        "spn_1482_transmission_source_address": (
            None if data[6] == BYTE_NOT_AVAILABLE else data[6]
        ),
        "spn_606_transmission_mode1": mode1,
        "spn_607_transmission_mode2": mode2,
        "spn_608_transmission_mode3": mode3,
        "spn_609_transmission_mode4": mode4,
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
    Byte 2 : SPN 521  Brake Pedal Position (0.4 %/bit)
    Byte 3 : SPN 576 / 577 / 1238 / 1792 (2'ser bit)
    Byte 4 : SPN 1438 / 575 / 1439 / 1793 (2'ser bit)
    Byte 5 : SPN 1481 Source Address of Controlling Device for Brake Control
    Byte 6 : SPN 2911 Total Brake Demand - Foundation Brakes (0.4 %/bit)
    Byte 7 : SPN 4251 / 4252 / 1836 / 4253 (2'ser bit)
    Byte 8 : kullanilmiyor (not available)

    Bayt 3-7 yerlesimi musteri DBC'sinden alinmistir; bayt 1-2 zaten
    J1939-71 ile ayni oldugu icin degismemistir.
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
            pack_2bit(
                _bit2(signals.get("abs_offroad_switch")),  # SPN 576
                _bit2(signals.get("asr_offroad_switch")),  # SPN 577
                _bit2(signals.get("asr_hill_holder_switch")),  # SPN 1238
                _bit2(signals.get("trailer_abs_status")),  # SPN 1792
            ),
            pack_2bit(
                _bit2(signals.get("ebs_red_warning")),  # SPN 1438
                _bit2(signals.get("abs_fully_operational")),  # SPN 575
                _bit2(signals.get("ebs_amber_warning")),  # SPN 1439
                _bit2(signals.get("atc_asr_information")),  # SPN 1793
            ),
            _source_address(signals.get("brake_source_address")),
            encode_percent(signals.get("total_brake_demand_pct")),
            pack_2bit(
                _bit2(signals.get("foundation_brakes_in_use")),  # SPN 4251
                _bit2(signals.get("halt_brake_switch")),  # SPN 4252
                _bit2(signals.get("trailer_connected")),  # SPN 1836
                _bit2(signals.get("halt_brake_mode")),  # SPN 4253
            ),
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_ebc1(data: bytes) -> dict:
    asr_engine, asr_brake, abs_active, ebs_switch = unpack_2bit(data[0])
    abs_offroad, asr_offroad, hill_holder, trailer_abs = unpack_2bit(data[2])
    red_warn, abs_full, amber_warn, atc_info = unpack_2bit(data[3])
    fdn_use, halt_switch, trailer_conn, halt_mode = unpack_2bit(data[6])
    return {
        "spn_521_brake_pedal_position_pct": decode_percent(data[1]),
        "spn_521_raw": data[1],
        "spn_561_asr_engine_control": asr_engine,
        "spn_562_asr_brake_control": asr_brake,
        "spn_563_abs_active": abs_active,
        "spn_1121_ebs_brake_switch": ebs_switch,
        "spn_576_abs_offroad_switch": abs_offroad,
        "spn_577_asr_offroad_switch": asr_offroad,
        "spn_1238_asr_hill_holder_switch": hill_holder,
        "spn_1792_trailer_abs_status": trailer_abs,
        "spn_1438_ebs_red_warning": red_warn,
        "spn_575_abs_fully_operational": abs_full,
        "spn_1439_ebs_amber_warning": amber_warn,
        "spn_1793_atc_asr_information": atc_info,
        "spn_1481_brake_source_address": None if data[4] == BYTE_NOT_AVAILABLE else data[4],
        "spn_2911_total_brake_demand_pct": decode_percent(data[5]),
        "spn_4251_foundation_brakes_in_use": fdn_use,
        "spn_4252_halt_brake_switch": halt_switch,
        "spn_1836_trailer_connected": trailer_conn,
        "spn_4253_halt_brake_mode": halt_mode,
    }


# --------------------------------------------------------------------------- #
# PGN 65215 - EBC2 (aks / tekerlek hizlari)
# --------------------------------------------------------------------------- #


def build_ebc2(signals: dict) -> bytes:
    """
    Byte 1-2: SPN 904 Front Axle Speed (1/256 km/h, little-endian)
    Byte 3 : SPN 905 Relative Speed, Front Axle Left  (1/16 km/h, offset -7.8125)
    Byte 4 : SPN 906 Relative Speed, Front Axle Right
    Byte 5 : SPN 907 Relative Speed, Rear Axle 1 Left
    Byte 6 : SPN 908 Relative Speed, Rear Axle 1 Right
    Byte 7 : SPN 909 Relative Speed, Rear Axle 2 Left
    Byte 8 : SPN 910 Relative Speed, Rear Axle 2 Right

    DBC bu mesaji PGN 64966 olarak yorumlar; J1939-71'de EBC2 PGN 65215'tir.
    Sinyal yerlesimi ve olcekleri DBC ile birebir aynidir.
    """
    front = encode_scaled(
        signals.get("front_axle_speed_kmh"), resolution=SPN84_RESOLUTION, byte_length=2
    )
    return bytes(
        (
            *_le(front),
            encode_rel_speed(signals.get("rel_speed_front_left")),
            encode_rel_speed(signals.get("rel_speed_front_right")),
            encode_rel_speed(signals.get("rel_speed_rear1_left")),
            encode_rel_speed(signals.get("rel_speed_rear1_right")),
            encode_rel_speed(signals.get("rel_speed_rear2_left")),
            encode_rel_speed(signals.get("rel_speed_rear2_right")),
        )
    )


def parse_ebc2(data: bytes) -> dict:
    return {
        "spn_904_front_axle_speed_kmh": decode_scaled(
            _word(data, 0), resolution=SPN84_RESOLUTION, byte_length=2
        ),
        "spn_905_rel_speed_front_left_kmh": decode_rel_speed(data[2]),
        "spn_906_rel_speed_front_right_kmh": decode_rel_speed(data[3]),
        "spn_907_rel_speed_rear1_left_kmh": decode_rel_speed(data[4]),
        "spn_908_rel_speed_rear1_right_kmh": decode_rel_speed(data[5]),
        "spn_909_rel_speed_rear2_left_kmh": decode_rel_speed(data[6]),
        "spn_910_rel_speed_rear2_right_kmh": decode_rel_speed(data[7]),
    }


# --------------------------------------------------------------------------- #
# PGN 65266 - LFE1 (yakit tuketimi / ekonomi)
# --------------------------------------------------------------------------- #


def build_lfe1(signals: dict) -> bytes:
    """
    Byte 1-2: SPN 183 Engine Fuel Rate (0.05 L/h per bit)
    Byte 3-4: SPN 184 Engine Instantaneous Fuel Economy (1/512 km/L per bit)
    Byte 5-6: SPN 185 Engine Average Fuel Economy (1/512 km/L per bit)
    Byte 7 : SPN 51   Engine Throttle Valve 1 Position (0.4 %/bit)
    Byte 8 : SPN 3673 Engine Throttle Valve 2 Position (kullanilmiyor)
    """
    return bytes(
        (
            *_le(
                encode_scaled(
                    signals.get("fuel_rate_lph"), resolution=FUEL_RATE_RESOLUTION, byte_length=2
                )
            ),
            *_le(
                encode_scaled(
                    signals.get("instant_fuel_economy_kmpl"),
                    resolution=FUEL_ECONOMY_RESOLUTION,
                    byte_length=2,
                )
            ),
            *_le(
                encode_scaled(
                    signals.get("average_fuel_economy_kmpl"),
                    resolution=FUEL_ECONOMY_RESOLUTION,
                    byte_length=2,
                )
            ),
            encode_percent(signals.get("throttle_valve_pct")),
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_lfe1(data: bytes) -> dict:
    return {
        "spn_183_fuel_rate_lph": decode_scaled(
            _word(data, 0), resolution=FUEL_RATE_RESOLUTION, byte_length=2, digits=2
        ),
        "spn_184_instant_fuel_economy_kmpl": decode_scaled(
            _word(data, 2), resolution=FUEL_ECONOMY_RESOLUTION, byte_length=2, digits=3
        ),
        "spn_185_average_fuel_economy_kmpl": decode_scaled(
            _word(data, 4), resolution=FUEL_ECONOMY_RESOLUTION, byte_length=2, digits=3
        ),
        "spn_51_throttle_valve_pct": decode_percent(data[6]),
    }


# --------------------------------------------------------------------------- #
# PGN 65262 - ET1 (motor sicakliklari)
# --------------------------------------------------------------------------- #


def build_et1(signals: dict) -> bytes:
    """
    Byte 1 : SPN 110 Engine Coolant Temperature (1 C/bit, offset -40)
    Byte 2 : SPN 174 Engine Fuel Temperature 1  (1 C/bit, offset -40)
    Byte 3-4: SPN 175 Engine Oil Temperature 1  (0.03125 C/bit, offset -273)
    Byte 5-6: SPN 176 Engine Turbocharger Oil Temperature
    Byte 7 : SPN 177  Engine Intercooler Temperature (kullanilmiyor)
    Byte 8 : SPN 2629 Turbo Compressor Outlet Temperature (kullanilmiyor)

    DBC, SPN 111 (sogutma suyu seviyesi) ve SPN 100 (yag basinci) sinyallerini
    de ET1'in 7. ve 8. baytina koyar; standartta ikisi de EFL/P1 (PGN 65263)
    alanidir ve oraya tasinmistir (bkz. build_eflp1).
    """
    return bytes(
        (
            encode_temp_byte(signals.get("coolant_temp_c")),
            encode_temp_byte(signals.get("fuel_temp_c")),
            *_le(encode_temp_word(signals.get("oil_temp_c"))),
            *_le(encode_temp_word(signals.get("turbo_oil_temp_c"))),
            BYTE_NOT_AVAILABLE,
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_et1(data: bytes) -> dict:
    return {
        "spn_110_coolant_temp_c": decode_temp_byte(data[0]),
        "spn_174_fuel_temp_c": decode_temp_byte(data[1]),
        "spn_175_oil_temp_c": decode_temp_word(_word(data, 2)),
        "spn_176_turbo_oil_temp_c": decode_temp_word(_word(data, 4)),
    }


# --------------------------------------------------------------------------- #
# PGN 65263 - EFL/P1 (motor sivi seviyesi / basinci)
# --------------------------------------------------------------------------- #


def build_eflp1(signals: dict) -> bytes:
    """
    Byte 1 : SPN 94  Engine Fuel Delivery Pressure (4 kPa/bit)
    Byte 2 : SPN 22  Extended Crankcase Blow-by Pressure (kullanilmiyor)
    Byte 3 : SPN 98  Engine Oil Level (0.4 %/bit)
    Byte 4 : SPN 100 Engine Oil Pressure (4 kPa/bit)
    Byte 5-6: SPN 101 Engine Crankcase Pressure (kullanilmiyor)
    Byte 7 : SPN 109 Engine Coolant Pressure (2 kPa/bit)
    Byte 8 : SPN 111 Engine Coolant Level (0.4 %/bit)
    """
    return bytes(
        (
            encode_pressure(signals.get("fuel_delivery_pressure_kpa"), PRESSURE_4KPA),
            BYTE_NOT_AVAILABLE,
            encode_percent(signals.get("oil_level_pct")),
            encode_pressure(signals.get("oil_pressure_kpa"), PRESSURE_4KPA),
            *_le(WORD_NOT_AVAILABLE),
            encode_pressure(signals.get("coolant_pressure_kpa"), PRESSURE_2KPA),
            encode_percent(signals.get("coolant_level_pct")),
        )
    )


def parse_eflp1(data: bytes) -> dict:
    return {
        "spn_94_fuel_delivery_pressure_kpa": decode_pressure(data[0], PRESSURE_4KPA),
        "spn_98_oil_level_pct": decode_percent(data[2]),
        "spn_100_oil_pressure_kpa": decode_pressure(data[3], PRESSURE_4KPA),
        "spn_109_coolant_pressure_kpa": decode_pressure(data[6], PRESSURE_2KPA),
        "spn_111_coolant_level_pct": decode_percent(data[7]),
    }


# --------------------------------------------------------------------------- #
# PGN 65270 - IC1 (emme / egzoz kosullari)
# --------------------------------------------------------------------------- #


def build_ic1(signals: dict) -> bytes:
    """
    Byte 1 : SPN 81  Particulate Trap Inlet Pressure (0.5 kPa/bit)
    Byte 2 : SPN 102 Engine Intake Manifold #1 Pressure - boost (2 kPa/bit)
    Byte 3 : SPN 105 Engine Intake Manifold 1 Temperature (1 C/bit, offset -40)
    Byte 4 : SPN 106 Engine Air Inlet Pressure (kullanilmiyor)
    Byte 5 : SPN 107 Engine Air Filter 1 Differential Pressure (0.05 kPa/bit)
    Byte 6-7: SPN 173 Engine Exhaust Gas Temperature (0.03125 C/bit, offset -273)
    Byte 8 : SPN 112 Engine Coolant Filter Differential Pressure (0.5 kPa/bit)

    DBC SPN 81'i 2 bayt / 0.05 kPa olarak tanimlar; standartta tek bayt ve
    0.5 kPa/bit oldugu icin standart yerlesim kullanilmistir.
    """
    return bytes(
        (
            encode_pressure(signals.get("particulate_trap_pressure_kpa"), PRESSURE_HALF_KPA),
            encode_pressure(signals.get("boost_pressure_kpa"), PRESSURE_2KPA),
            encode_temp_byte(signals.get("intake_manifold_temp_c")),
            BYTE_NOT_AVAILABLE,
            encode_pressure(signals.get("air_filter_diff_pressure_kpa"), PRESSURE_005KPA),
            *_le(encode_temp_word(signals.get("exhaust_gas_temp_c"))),
            encode_pressure(signals.get("coolant_filter_diff_pressure_kpa"), PRESSURE_HALF_KPA),
        )
    )


def parse_ic1(data: bytes) -> dict:
    return {
        "spn_81_particulate_trap_pressure_kpa": decode_pressure(data[0], PRESSURE_HALF_KPA),
        "spn_102_boost_pressure_kpa": decode_pressure(data[1], PRESSURE_2KPA),
        "spn_105_intake_manifold_temp_c": decode_temp_byte(data[2]),
        "spn_107_air_filter_diff_pressure_kpa": decode_pressure(data[4], PRESSURE_005KPA),
        "spn_173_exhaust_gas_temp_c": decode_temp_word(_word(data, 5)),
        "spn_112_coolant_filter_diff_pressure_kpa": decode_pressure(data[7], PRESSURE_HALF_KPA),
    }


# --------------------------------------------------------------------------- #
# PGN 65269 - AMB (ortam kosullari)
# --------------------------------------------------------------------------- #


def build_amb(signals: dict) -> bytes:
    """
    Byte 1 : SPN 108 Barometric Pressure (0.5 kPa/bit)
    Byte 2-3: SPN 170 Cab Interior Temperature (0.03125 C/bit, offset -273)
    Byte 4-5: SPN 171 Ambient Air Temperature
    Byte 6 : SPN 172 Air Inlet Temperature (1 C/bit, offset -40)
    Byte 7-8: SPN 79 Road Surface Temperature
    """
    return bytes(
        (
            encode_pressure(signals.get("barometric_pressure_kpa"), PRESSURE_HALF_KPA),
            *_le(encode_temp_word(signals.get("cab_interior_temp_c"))),
            *_le(encode_temp_word(signals.get("ambient_air_temp_c"))),
            encode_temp_byte(signals.get("air_inlet_temp_c")),
            *_le(encode_temp_word(signals.get("road_surface_temp_c"))),
        )
    )


def parse_amb(data: bytes) -> dict:
    return {
        "spn_108_barometric_pressure_kpa": decode_pressure(data[0], PRESSURE_HALF_KPA),
        "spn_170_cab_interior_temp_c": decode_temp_word(_word(data, 1)),
        "spn_171_ambient_air_temp_c": decode_temp_word(_word(data, 3)),
        "spn_172_air_inlet_temp_c": decode_temp_byte(data[5]),
        "spn_79_road_surface_temp_c": decode_temp_word(_word(data, 6)),
    }


# --------------------------------------------------------------------------- #
# PGN 65276 - DD (Dash Display)
# --------------------------------------------------------------------------- #


def build_dd(signals: dict) -> bytes:
    """
    Byte 1 : SPN 80 Washer Fluid Level (0.4 %/bit)
    Byte 2 : SPN 96 Fuel Level 1 (0.4 %/bit)
    Byte 3 : SPN 1856 / 1883 / 1420 (2'ser bit) - DBC uzantisi
    Byte 4 : SPN 99  Engine Oil Filter Differential Pressure (kullanilmiyor)
    Byte 5-6: SPN 169 Cargo Ambient Temperature (0.03125 C/bit, offset -273)
    Byte 7 : SPN 38  Fuel Level 2 (0.4 %/bit)
    Byte 8 : kullanilmiyor (not available)

    Bayt 3'teki emniyet kemeri / dis aydinlatma / bakim lambasi sinyalleri
    standart DD taniminda yoktur; DBC'de bulunduklari ve o bayt (standartta
    SPN 95) DBC tarafindan kullanilmadigi icin oraya yerlestirilmislerdir.

    DBC'nin SPN 352/353 (aks konumu / aks agirligi) sinyalleri yayinlanmaz:
    PGN 65258 (VW) alanidirlar ve DBC'deki 52|16 yerlesimi 8 bayta tasar.
    """
    return bytes(
        (
            encode_percent(signals.get("washer_fluid_level_pct")),
            encode_percent(signals.get("fuel_level_pct")),
            pack_2bit(
                _bit2(signals.get("seat_belt_fastened")),  # SPN 1856
                _bit2(signals.get("exterior_light_on")),  # SPN 1883
                _bit2(signals.get("maintenance_lamp_on")),  # SPN 1420
                BIT2_NOT_AVAILABLE,
            ),
            BYTE_NOT_AVAILABLE,
            *_le(encode_temp_word(signals.get("cargo_ambient_temp_c"))),
            encode_percent(signals.get("fuel_level2_pct")),
            BYTE_NOT_AVAILABLE,
        )
    )


def parse_dd(data: bytes) -> dict:
    seat_belt, lantern, maint_lamp, _reserved = unpack_2bit(data[2])
    return {
        "spn_80_washer_fluid_level_pct": decode_percent(data[0]),
        "spn_96_fuel_level_pct": decode_percent(data[1]),
        "spn_1856_seat_belt_fastened": seat_belt,
        "spn_1883_exterior_light_on": lantern,
        "spn_1420_maintenance_lamp_on": maint_lamp,
        "spn_169_cargo_ambient_temp_c": decode_temp_word(_word(data, 4)),
        "spn_38_fuel_level2_pct": decode_percent(data[6]),
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
    """
    Byte 1-4: SPN 247 Engine Total Hours of Operation (0.05 h/bit)
    Byte 5-8: SPN 248 Total Power Takeoff (PTO) Hours (0.05 h/bit)
    """
    engine = _encode_scaled32(signals.get("engine_hours"), resolution=ENGINE_HOURS_RESOLUTION)
    pto = _encode_scaled32(signals.get("pto_hours"), resolution=ENGINE_HOURS_RESOLUTION)
    return engine.to_bytes(4, "little") + pto.to_bytes(4, "little")


def parse_hours(data: bytes) -> dict:
    engine_raw = int.from_bytes(data[0:4], "little")
    pto_raw = int.from_bytes(data[4:8], "little")
    return {
        "spn_247_engine_hours": _decode_scaled32(
            engine_raw, resolution=ENGINE_HOURS_RESOLUTION, digits=2
        ),
        "spn_248_pto_hours": _decode_scaled32(
            pto_raw, resolution=ENGINE_HOURS_RESOLUTION, digits=2
        ),
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
            "598 Clutch Switch",
            "70 Parking Brake",
            "595/596 Cruise Active / Enable",
            "599-602 Cruise Set/Coast/Resume/Accel",
            "976 PTO Governor State",
        ),
    ),
    PGN_CCSS: MessageDef(
        pgn=PGN_CCSS,
        acronym="CCSS",
        name="Cruise Control / Vehicle Speed Setup",
        priority=6,
        transmit_rate_ms=5000,
        builder=build_ccss,
        parser=parse_ccss,
        spns=(
            "1085 Cruise High Set Limit",
            "1086 Cruise Low Set Limit",
            "1087 Maximum Vehicle Speed Limit",
        ),
    ),
    PGN_EEC1: MessageDef(
        pgn=PGN_EEC1,
        acronym="EEC1",
        name="Electronic Engine Controller 1",
        priority=3,
        transmit_rate_ms=20,
        builder=build_eec1,
        parser=parse_eec1,
        spns=(
            "190 Engine Speed",
            "512 Driver's Demand Torque",
            "513 Actual Engine Torque",
            "2432 Engine Demand Torque",
            "899 Engine Torque Mode",
            "1675 Engine Starter Mode",
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
            "539 Actual Max Available Torque",
            "1481 Estimated Parasitic Losses",
        ),
    ),
    PGN_ETC1: MessageDef(
        pgn=PGN_ETC1,
        acronym="ETC1",
        name="Electronic Transmission Controller 1",
        priority=3,
        transmit_rate_ms=20,
        builder=build_etc1,
        parser=parse_etc1,
        spns=(
            "191 Output Shaft Speed",
            "161 Input Shaft Speed",
            "522 Percent Clutch Slip",
            "560 Driveline Engaged",
            "573 Torque Converter Lockup",
            "574 Shift In Process",
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
        spns=(
            "521 Brake Pedal Position",
            "563 ABS Active",
            "1121 EBS Brake Switch",
            "2911 Total Brake Demand",
            "1438/1439 EBS Red / Amber Warning",
            "1836 Trailer Connected",
        ),
    ),
    PGN_EBC2: MessageDef(
        pgn=PGN_EBC2,
        acronym="EBC2",
        name="Electronic Brake Controller 2",
        priority=6,
        transmit_rate_ms=100,
        builder=build_ebc2,
        parser=parse_ebc2,
        spns=(
            "904 Front Axle Speed",
            "905/906 Front Axle Relative Speeds",
            "907/908 Rear Axle 1 Relative Speeds",
            "909/910 Rear Axle 2 Relative Speeds",
        ),
    ),
    PGN_LFE1: MessageDef(
        pgn=PGN_LFE1,
        acronym="LFE1",
        name="Engine Fluid Level / Fuel Economy",
        priority=6,
        transmit_rate_ms=100,
        builder=build_lfe1,
        parser=parse_lfe1,
        spns=(
            "183 Engine Fuel Rate",
            "184 Instantaneous Fuel Economy",
            "185 Average Fuel Economy",
            "51 Throttle Valve Position",
        ),
    ),
    PGN_ET1: MessageDef(
        pgn=PGN_ET1,
        acronym="ET1",
        name="Engine Temperature 1",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_et1,
        parser=parse_et1,
        spns=(
            "110 Engine Coolant Temperature",
            "174 Engine Fuel Temperature",
            "175 Engine Oil Temperature",
            "176 Turbocharger Oil Temperature",
        ),
    ),
    PGN_EFLP1: MessageDef(
        pgn=PGN_EFLP1,
        acronym="EFLP1",
        name="Engine Fluid Level / Pressure 1",
        priority=6,
        transmit_rate_ms=500,
        builder=build_eflp1,
        parser=parse_eflp1,
        spns=(
            "100 Engine Oil Pressure",
            "98 Engine Oil Level",
            "111 Engine Coolant Level",
            "94 Fuel Delivery Pressure",
            "109 Engine Coolant Pressure",
        ),
    ),
    PGN_IC1: MessageDef(
        pgn=PGN_IC1,
        acronym="IC1",
        name="Inlet / Exhaust Conditions 1",
        priority=6,
        transmit_rate_ms=500,
        builder=build_ic1,
        parser=parse_ic1,
        spns=(
            "102 Boost Pressure",
            "105 Intake Manifold Temperature",
            "173 Exhaust Gas Temperature",
            "81 Particulate Trap Inlet Pressure",
            "107 Air Filter Differential Pressure",
            "112 Coolant Filter Differential Pressure",
        ),
    ),
    PGN_AMB: MessageDef(
        pgn=PGN_AMB,
        acronym="AMB",
        name="Ambient Conditions",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_amb,
        parser=parse_amb,
        spns=(
            "108 Barometric Pressure",
            "170 Cab Interior Temperature",
            "171 Ambient Air Temperature",
            "172 Air Inlet Temperature",
            "79 Road Surface Temperature",
        ),
    ),
    PGN_DD: MessageDef(
        pgn=PGN_DD,
        acronym="DD",
        name="Dash Display",
        priority=6,
        transmit_rate_ms=1000,
        builder=build_dd,
        parser=parse_dd,
        spns=(
            "96 Fuel Level 1",
            "38 Fuel Level 2",
            "80 Washer Fluid Level",
            "169 Cargo Ambient Temperature",
            "1856 Seat Belt Switch",
            "1420 Maintenance Lamp",
        ),
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
        spns=("247 Engine Total Hours of Operation", "248 Total PTO Hours"),
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
