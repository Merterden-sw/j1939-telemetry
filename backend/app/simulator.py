"""
J1939 arac telemetri simulatoru.

Her arac icin bagimsiz bir surus durumu tutulur; sabit periyotlu (varsayilan
100 ms) bir dongude fizik guncellenir ve kayit defterindeki her mesaj kendi
yayin periyoduna gore uretilir:

    CCVS1  100 ms   hiz, fren anahtari, el freni, cruise
    EEC2    50 ms   gaz pedali, motor yuku
    ETC2   100 ms   vites ve kademe (P/R/N/D)
    EBC1   100 ms   fren pedali konumu, ABS
    HVBATT 1000 ms  batarya SOC / SOH
    DM1    1000 ms  aktif ariza kodlari (basitlestirilmis, tek DTC)
    VEP1   1000 ms  konum (enlem/boylam)
    HOURS  5000 ms  motor calisma saati
    VDHR   1000 ms  trip / toplam mesafe

Uretilen cerceveler bir geri cagirma uzerinden yayin katmanina aktarilir;
simulatorun agdan haberi yoktur.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from .config import Settings
from .fleet import Fleet, Vehicle
from .j1939 import FAULT_POOL, FMI_NAMES, MESSAGES, PGN_CCVS1, SPN_NAMES, build_frame

logger = logging.getLogger(__name__)

Mode = str  # "manual" | "auto" | "idle"
VALID_MODES: tuple[Mode, ...] = ("manual", "auto", "idle")
VALID_RANGES: tuple[str, ...] = ("P", "R", "N", "D")

REVERSE_MAX_KMH = 20.0
BRAKE_PEDAL_ON_PCT = 60.0

# --------------------------------------------------------------------------- #
# Turetilmis sinyal modellerinin katsayilari
# --------------------------------------------------------------------------- #

# Termal zaman sabitleri (saniye): sogutma suyu yavas, egzoz hizli tepki verir.
COOLANT_TAU_S = 90.0
OIL_TAU_S = 150.0
EXHAUST_TAU_S = 8.0
COOLANT_NOMINAL_C = 87.0  # termostat sonrasi calisma sicakligi

# Yakit modeli
IDLE_FUEL_LPH = 2.0  # rolantide tuketim
FUEL_LITRE_PER_KWH = 0.26  # yaklasik dizel BSFC (0.21 kg/kWh, 0.84 kg/L)
FUEL_TANK_L = 400.0  # agir ticari arac depo hacmi (tek depo)

# Vites degisimi penceresi (SPN 574 / 598 / 522)
SHIFT_DURATION_S = 0.6

# Sanziman orani araligi (SPN 526): agir ticari 12 ileri vitesli kutu
GEAR_RATIO_FIRST = 11.7  # en dusuk vites
GEAR_RATIO_TOP = 0.78  # en yuksek vites (overdrive)

# ABS tetikleme esikleri (SPN 563)
ABS_TRIGGER_PEDAL_PCT = 75.0
ABS_MIN_SPEED_KMH = 20.0
ABS_HOLD_S = 1.5

# Bagil tekerlek hizi sacilimi (SPN 905-910), km/h
WHEEL_SLIP_KMH = 0.35
WHEEL_SLIP_ABS_KMH = 2.5

# Ortam sicakligi surunmesi (C / saniye)
AMBIENT_DRIFT_C = 0.02

# Bakim ve sarf takibi
SERVICE_INTERVAL_KM = 40_000.0
SERVICE_WARN_KM = 38_000.0
AIR_FILTER_CLOG_KPA_PER_HOUR = 0.0025
WASHER_USE_PCT_PER_HOUR = 0.5

FrameCallback = Callable[[list[dict]], Awaitable[None]]


class SimulatorError(ValueError):
    """Gecersiz simulator komutu."""


@dataclass
class VehicleState:
    """Tek bir aracin calisma zamani durumu."""

    vehicle_id: str

    # Hareket
    speed_kmh: float = 0.0
    target_speed_kmh: float = 0.0
    accel_pedal_pct: float = 0.0
    brake_pedal_pct: float = 0.0
    control_source: str = "speed"  # "speed" (hiz enjeksiyonu) | "pedal"

    # Aktarma organi
    gear: int = 0
    gear_range: str = "D"
    gear_auto: bool = True
    gear_count: int = 12

    # Motor
    engine_rpm: float = 0.0
    engine_load_pct: float = 0.0

    # Batarya
    soc_pct: float = 85.0
    soh_pct: float = 98.0

    # Motor torku (EEC1)
    driver_demand_torque_pct: float = 0.0
    actual_engine_torque_pct: float = 0.0
    demand_engine_torque_pct: float = 0.0
    max_available_torque_pct: float = 100.0
    parasitic_losses_pct: float = 5.0
    torque_mode: int = 0
    starter_mode: int = 0

    # Aktarma organi (ETC1)
    input_shaft_rpm: float = 0.0
    output_shaft_rpm: float = 0.0
    clutch_slip_pct: float = 0.0
    shift_in_process: bool = False
    torque_converter_lockup: bool = False
    driveline_engaged: bool = False
    _shift_timer: float = 0.0
    _last_gear: int = 0

    # Motor sicakliklari (ET1 / IC1)
    coolant_temp_c: float = 20.0
    oil_temp_c: float = 20.0
    turbo_oil_temp_c: float = 20.0
    fuel_temp_c: float = 20.0
    intake_manifold_temp_c: float = 20.0
    exhaust_gas_temp_c: float = 20.0

    # Basinclar (EFLP1 / IC1)
    oil_pressure_kpa: float = 0.0
    boost_pressure_kpa: float = 0.0
    fuel_delivery_pressure_kpa: float = 0.0
    coolant_pressure_kpa: float = 0.0
    particulate_trap_pressure_kpa: float = 0.0
    air_filter_diff_pressure_kpa: float = 0.4
    coolant_filter_diff_pressure_kpa: float = 2.0

    # Sivi seviyeleri (EFLP1 / DD)
    oil_level_pct: float = 90.0
    coolant_level_pct: float = 95.0
    fuel_level_pct: float = 80.0
    fuel_level2_pct: float = 80.0
    washer_fluid_level_pct: float = 85.0

    # Yakit tuketimi (LFE1)
    fuel_rate_lph: float = 0.0
    instant_fuel_economy_kmpl: float = 0.0
    average_fuel_economy_kmpl: float = 0.0
    throttle_valve_pct: float = 0.0
    fuel_used_l: float = 0.0

    # Ortam kosullari (AMB / DD)
    ambient_air_temp_c: float = 18.0
    cab_interior_temp_c: float = 21.0
    road_surface_temp_c: float = 20.0
    air_inlet_temp_c: float = 18.0
    cargo_ambient_temp_c: float = 8.0
    barometric_pressure_kpa: float = 101.0

    # Fren sistemi (EBC1 / EBC2)
    abs_active: bool = False
    abs_fully_operational: bool = True
    ebs_red_warning: bool = False
    ebs_amber_warning: bool = False
    atc_asr_information: bool = False
    asr_engine_control: bool = False
    asr_brake_control: bool = False
    foundation_brakes_in_use: bool = False
    trailer_connected: bool = False
    total_brake_demand_pct: float = 0.0
    wheel_slip: tuple = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    _abs_timer: float = 0.0

    # Cruise anahtarlari (CCVS1 / CCSS)
    cruise_set_switch: bool = False
    cruise_coast_switch: bool = False
    cruise_resume_switch: bool = False
    cruise_accel_switch: bool = False
    cruise_high_limit_kmh: int = 90
    cruise_low_limit_kmh: int = 30

    # Gosterge paneli (DD) ve PTO
    seat_belt_fastened: bool = True
    exterior_light_on: bool = False
    maintenance_lamp_on: bool = False
    pto_state: int = 0
    pto_hours: float = 0.0

    # Anahtarlar / mod
    mode: Mode = "manual"
    online: bool = True
    parking_brake: bool = False
    cruise_active: bool = False
    cruise_set_speed_kmh: int | None = None

    # Filo telematik: konum, calisma saati, mesafe, ariza kodlari
    latitude: float = 0.0
    longitude: float = 0.0
    heading_deg: float = 0.0
    engine_hours: float = 0.0
    trip_km: float = 0.0
    odometer_km: float = 0.0
    active_dtcs: list = field(default_factory=list)

    # Sayaclar
    tx_counter: int = 0
    last_frames: dict[int, dict] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    _auto_timer: float = 0.0

    @property
    def brake(self) -> bool:
        """Fren anahtari (SPN 597) - pedala basiliysa acik."""
        return self.brake_pedal_pct > 0.5

    @property
    def clutch_switch(self) -> bool:
        """Debriyaj anahtari (SPN 598) - vites degisimi sirasinda acik."""
        return self.shift_in_process

    def signals(self, source_address: int | None = None) -> dict:
        """Mesaj kuruculara verilecek tam sinyal sozlugu."""
        return {
            # CCVS1
            "speed_kmh": self.speed_kmh,
            "parking_brake": self.parking_brake,
            "brake_switch": self.brake,
            "clutch_switch": self.clutch_switch,
            "cruise_active": self.cruise_active,
            "cruise_enable": self.cruise_active or self.cruise_set_speed_kmh is not None,
            "cruise_set_speed_kmh": self.cruise_set_speed_kmh,
            "cruise_set_switch": self.cruise_set_switch,
            "cruise_coast_switch": self.cruise_coast_switch,
            "cruise_resume_switch": self.cruise_resume_switch,
            "cruise_accel_switch": self.cruise_accel_switch,
            "pto_state": self.pto_state,
            # CCSS
            "cruise_high_limit_kmh": self.cruise_high_limit_kmh,
            "cruise_low_limit_kmh": self.cruise_low_limit_kmh,
            "max_speed_limit_kmh": self.cruise_high_limit_kmh,
            # EEC1
            "engine_rpm": self.engine_rpm,
            "driver_demand_torque_pct": self.driver_demand_torque_pct,
            "actual_engine_torque_pct": self.actual_engine_torque_pct,
            "demand_engine_torque_pct": self.demand_engine_torque_pct,
            "torque_mode": self.torque_mode,
            "starter_mode": self.starter_mode,
            "engine_source_address": source_address,
            # EEC2
            "accel_pedal_pct": self.accel_pedal_pct,
            "engine_load_pct": self.engine_load_pct,
            "remote_accel_pct": None,
            "accel_pedal2_pct": None,
            "kickdown": self.accel_pedal_pct > 95,
            "max_available_torque_pct": self.max_available_torque_pct,
            "parasitic_losses_pct": self.parasitic_losses_pct,
            # ETC1
            "output_shaft_rpm": self.output_shaft_rpm,
            "input_shaft_rpm": self.input_shaft_rpm,
            "clutch_slip_pct": self.clutch_slip_pct,
            "driveline_engaged": self.driveline_engaged,
            "torque_converter_lockup": self.torque_converter_lockup,
            "shift_in_process": self.shift_in_process,
            "transmission_source_address": source_address,
            "transmission_mode1": self.gear_auto,
            "transmission_mode2": False,
            "transmission_mode3": False,
            "transmission_mode4": False,
            # ETC2
            "current_gear": self.gear,
            "selected_gear": self.gear,
            "gear_ratio": self._gear_ratio(),
            "current_range": self.gear_range,
            "requested_range": self.gear_range,
            # EBC1
            "brake_pedal_pct": self.brake_pedal_pct,
            "abs_active": self.abs_active,
            "ebs_brake_switch": self.brake,
            "asr_engine_control": self.asr_engine_control,
            "asr_brake_control": self.asr_brake_control,
            "abs_offroad_switch": False,
            "asr_offroad_switch": False,
            "asr_hill_holder_switch": self.parking_brake,
            "trailer_abs_status": self.trailer_connected and self.abs_active,
            "ebs_red_warning": self.ebs_red_warning,
            "ebs_amber_warning": self.ebs_amber_warning,
            "abs_fully_operational": self.abs_fully_operational,
            "atc_asr_information": self.atc_asr_information,
            "brake_source_address": source_address,
            "total_brake_demand_pct": self.total_brake_demand_pct,
            "foundation_brakes_in_use": self.foundation_brakes_in_use,
            "halt_brake_switch": self.parking_brake,
            "trailer_connected": self.trailer_connected,
            "halt_brake_mode": self.parking_brake,
            # EBC2
            "front_axle_speed_kmh": self.speed_kmh,
            "rel_speed_front_left": self.wheel_slip[0],
            "rel_speed_front_right": self.wheel_slip[1],
            "rel_speed_rear1_left": self.wheel_slip[2],
            "rel_speed_rear1_right": self.wheel_slip[3],
            "rel_speed_rear2_left": self.wheel_slip[4],
            "rel_speed_rear2_right": self.wheel_slip[5],
            # LFE1
            "fuel_rate_lph": self.fuel_rate_lph,
            "instant_fuel_economy_kmpl": self.instant_fuel_economy_kmpl,
            "average_fuel_economy_kmpl": self.average_fuel_economy_kmpl,
            "throttle_valve_pct": self.throttle_valve_pct,
            # ET1
            "coolant_temp_c": self.coolant_temp_c,
            "fuel_temp_c": self.fuel_temp_c,
            "oil_temp_c": self.oil_temp_c,
            "turbo_oil_temp_c": self.turbo_oil_temp_c,
            # EFLP1
            "oil_pressure_kpa": self.oil_pressure_kpa,
            "oil_level_pct": self.oil_level_pct,
            "coolant_level_pct": self.coolant_level_pct,
            "fuel_delivery_pressure_kpa": self.fuel_delivery_pressure_kpa,
            "coolant_pressure_kpa": self.coolant_pressure_kpa,
            # IC1
            "boost_pressure_kpa": self.boost_pressure_kpa,
            "intake_manifold_temp_c": self.intake_manifold_temp_c,
            "exhaust_gas_temp_c": self.exhaust_gas_temp_c,
            "particulate_trap_pressure_kpa": self.particulate_trap_pressure_kpa,
            "air_filter_diff_pressure_kpa": self.air_filter_diff_pressure_kpa,
            "coolant_filter_diff_pressure_kpa": self.coolant_filter_diff_pressure_kpa,
            # AMB
            "barometric_pressure_kpa": self.barometric_pressure_kpa,
            "cab_interior_temp_c": self.cab_interior_temp_c,
            "ambient_air_temp_c": self.ambient_air_temp_c,
            "air_inlet_temp_c": self.air_inlet_temp_c,
            "road_surface_temp_c": self.road_surface_temp_c,
            # DD
            "washer_fluid_level_pct": self.washer_fluid_level_pct,
            "fuel_level_pct": self.fuel_level_pct,
            "fuel_level2_pct": self.fuel_level2_pct,
            "cargo_ambient_temp_c": self.cargo_ambient_temp_c,
            "seat_belt_fastened": self.seat_belt_fastened,
            "exterior_light_on": self.exterior_light_on,
            "maintenance_lamp_on": self.maintenance_lamp_on,
            # HVBATT
            "soc_pct": self.soc_pct,
            "soh_pct": self.soh_pct,
            # DM1
            "dtc_spn": self.active_dtcs[-1]["spn"] if self.active_dtcs else None,
            "dtc_fmi": self.active_dtcs[-1]["fmi"] if self.active_dtcs else None,
            "dtc_occurrence_count": self.active_dtcs[-1]["occurrence_count"]
            if self.active_dtcs
            else 1,
            # VEP1
            "latitude_deg": self.latitude,
            "longitude_deg": self.longitude,
            # HOURS
            "engine_hours": self.engine_hours,
            "pto_hours": self.pto_hours,
            # VDHR
            "trip_km": self.trip_km,
            "total_km": self.odometer_km,
        }

    def _gear_ratio(self) -> float | None:
        """
        Guncel aktarma orani (SPN 526).

        Vites oranlari geometrik bir seriyle modellenir: en dusuk viteste
        GEAR_RATIO_FIRST, en yuksek viteste GEAR_RATIO_TOP (overdrive). Bu,
        ETC1'deki giris/cikis mili devirlerinin (SPN 161/191) de fiziksel
        olarak tutarli cikmasini saglar.
        """
        if self.gear == 0:
            return 0.0
        if self.gear < 0:  # geri vites, birinci vitesle ayni kademe
            return round(GEAR_RATIO_FIRST, 3)
        if self.gear_count <= 1:
            return round(GEAR_RATIO_TOP, 3)
        step = (self.gear_count - min(self.gear, self.gear_count)) / (self.gear_count - 1)
        return round(GEAR_RATIO_TOP * (GEAR_RATIO_FIRST / GEAR_RATIO_TOP) ** step, 3)

    def to_dict(self, include_frames: bool = True) -> dict:
        payload = {
            "vehicle_id": self.vehicle_id,
            "speed_kmh": round(self.speed_kmh, 2),
            "target_speed_kmh": round(self.target_speed_kmh, 2),
            "accel_pedal_pct": round(self.accel_pedal_pct, 1),
            "brake_pedal_pct": round(self.brake_pedal_pct, 1),
            "control_source": self.control_source,
            "gear": self.gear,
            "gear_range": self.gear_range,
            "gear_auto": self.gear_auto,
            "engine_rpm": round(self.engine_rpm),
            "engine_load_pct": round(self.engine_load_pct, 1),
            "soc_pct": round(self.soc_pct, 1),
            "soh_pct": round(self.soh_pct, 1),
            "mode": self.mode,
            "online": self.online,
            "brake": self.brake,
            "parking_brake": self.parking_brake,
            "cruise_active": self.cruise_active,
            "cruise_set_speed_kmh": self.cruise_set_speed_kmh,
            "tx_counter": self.tx_counter,
            "updated_at": self.updated_at,
            "latitude": round(self.latitude, 7),
            "longitude": round(self.longitude, 7),
            "engine_hours": round(self.engine_hours, 2),
            "trip_km": round(self.trip_km, 3),
            "odometer_km": round(self.odometer_km, 3),
            "active_dtcs": list(self.active_dtcs),
            # --- DBC sinyalleri: sistem sekmelerinde gosterilir ---------- #
            "engine": {
                "driver_demand_torque_pct": round(self.driver_demand_torque_pct, 1),
                "actual_engine_torque_pct": round(self.actual_engine_torque_pct, 1),
                "demand_engine_torque_pct": round(self.demand_engine_torque_pct, 1),
                "max_available_torque_pct": round(self.max_available_torque_pct, 1),
                "parasitic_losses_pct": round(self.parasitic_losses_pct, 1),
                "torque_mode": self.torque_mode,
                "starter_mode": self.starter_mode,
                "coolant_temp_c": round(self.coolant_temp_c, 1),
                "oil_temp_c": round(self.oil_temp_c, 1),
                "turbo_oil_temp_c": round(self.turbo_oil_temp_c, 1),
                "fuel_temp_c": round(self.fuel_temp_c, 1),
                "oil_pressure_kpa": round(self.oil_pressure_kpa, 1),
                "oil_level_pct": round(self.oil_level_pct, 1),
                "coolant_level_pct": round(self.coolant_level_pct, 1),
                "coolant_pressure_kpa": round(self.coolant_pressure_kpa, 1),
                "fuel_delivery_pressure_kpa": round(self.fuel_delivery_pressure_kpa, 1),
                "fuel_rate_lph": round(self.fuel_rate_lph, 2),
                "instant_fuel_economy_kmpl": round(self.instant_fuel_economy_kmpl, 2),
                "average_fuel_economy_kmpl": round(self.average_fuel_economy_kmpl, 2),
                "throttle_valve_pct": round(self.throttle_valve_pct, 1),
                "boost_pressure_kpa": round(self.boost_pressure_kpa, 1),
                "intake_manifold_temp_c": round(self.intake_manifold_temp_c, 1),
                "exhaust_gas_temp_c": round(self.exhaust_gas_temp_c, 1),
                "particulate_trap_pressure_kpa": round(self.particulate_trap_pressure_kpa, 2),
                "air_filter_diff_pressure_kpa": round(self.air_filter_diff_pressure_kpa, 2),
                "coolant_filter_diff_pressure_kpa": round(self.coolant_filter_diff_pressure_kpa, 2),
            },
            "transmission": {
                "input_shaft_rpm": round(self.input_shaft_rpm),
                "output_shaft_rpm": round(self.output_shaft_rpm),
                "clutch_slip_pct": round(self.clutch_slip_pct, 1),
                "gear_ratio": self._gear_ratio(),
                "shift_in_process": self.shift_in_process,
                "torque_converter_lockup": self.torque_converter_lockup,
                "driveline_engaged": self.driveline_engaged,
            },
            "brakes": {
                "abs_active": self.abs_active,
                "abs_fully_operational": self.abs_fully_operational,
                "asr_engine_control": self.asr_engine_control,
                "asr_brake_control": self.asr_brake_control,
                "atc_asr_information": self.atc_asr_information,
                "ebs_red_warning": self.ebs_red_warning,
                "ebs_amber_warning": self.ebs_amber_warning,
                "foundation_brakes_in_use": self.foundation_brakes_in_use,
                "total_brake_demand_pct": round(self.total_brake_demand_pct, 1),
                "trailer_connected": self.trailer_connected,
                "wheel_slip": list(self.wheel_slip),
            },
            "ambient": {
                "ambient_air_temp_c": round(self.ambient_air_temp_c, 1),
                "cab_interior_temp_c": round(self.cab_interior_temp_c, 1),
                "road_surface_temp_c": round(self.road_surface_temp_c, 1),
                "air_inlet_temp_c": round(self.air_inlet_temp_c, 1),
                "cargo_ambient_temp_c": round(self.cargo_ambient_temp_c, 1),
                "barometric_pressure_kpa": round(self.barometric_pressure_kpa, 1),
                "fuel_level_pct": round(self.fuel_level_pct, 1),
                "fuel_level2_pct": round(self.fuel_level2_pct, 1),
                "washer_fluid_level_pct": round(self.washer_fluid_level_pct, 1),
                "seat_belt_fastened": self.seat_belt_fastened,
                "exterior_light_on": self.exterior_light_on,
                "maintenance_lamp_on": self.maintenance_lamp_on,
                "pto_state": self.pto_state,
                "pto_hours": round(self.pto_hours, 2),
            },
        }
        if include_frames:
            payload["last_frames"] = self.last_frames
            payload["last_frame"] = self.last_frames.get(PGN_CCVS1)
        return payload


class Simulator:
    """Filo genelinde J1939 trafigi ureten asenkron simulator."""

    def __init__(self, fleet: Fleet, settings: Settings) -> None:
        self.fleet = fleet
        self.settings = settings
        self.states: dict[str, VehicleState] = {}
        self._rng = random.Random(20260902)
        # Konum/ariza rastgeleligi icin ayri RNG: mevcut soc/soh ve auto-mode
        # cekimlerinin sirasini bozmamak icin.
        self._telemetry_rng = random.Random(20260903)

        for vehicle in fleet:
            # Dizel araclarda SPN 5464 starter akusunu temsil eder ve alternator
            # sayesinde hep dolu gezer; elektrikli/hibritlerde surus bataryasidir.
            soc = (
                self._rng.uniform(94.0, 99.0)
                if vehicle.powertrain == "diesel"
                else self._rng.uniform(45.0, 95.0)
            )
            state = VehicleState(
                vehicle_id=vehicle.id,
                soc_pct=soc,
                soh_pct=self._rng.uniform(88.0, 100.0),
            )
            self._seed_position(state, vehicle)
            self._seed_environment(state, vehicle)
            self.states[vehicle.id] = state

        # Her mesajin kac tick'te bir yayinlanacagi
        self._intervals = {
            pgn: max(1, round(msg.transmit_rate_ms / settings.tick_ms))
            for pgn, msg in MESSAGES.items()
        }

        self._task: asyncio.Task | None = None
        self._callbacks: list[FrameCallback] = []
        self._running = False
        self._tick_count = 0
        self._started_at = time.time()

    # ------------------------------------------------------------------ #
    # Yasam dongusu
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._running

    def on_frames(self, callback: FrameCallback) -> None:
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        self._task = asyncio.create_task(self._run(), name="j1939-simulator")
        logger.info(
            "Simulator basladi: %d arac, %d mesaj, %d ms periyot",
            len(self.states),
            len(MESSAGES),
            self.settings.tick_ms,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("Simulator durdu")

    async def _run(self) -> None:
        period = self.settings.tick_seconds
        next_tick = time.perf_counter()
        while self._running:
            next_tick += period
            try:
                frames = self.tick()
                if frames:
                    for callback in list(self._callbacks):
                        await callback(frames)
            except Exception:  # pragma: no cover - dongu asla olmemeli
                logger.exception("Simulator tick hatasi")

            delay = next_tick - time.perf_counter()
            if delay < 0:
                next_tick = time.perf_counter()
                delay = 0
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------ #
    # Simulasyon adimi
    # ------------------------------------------------------------------ #
    def tick(self, dt: float | None = None) -> list[dict]:
        """Bir simulasyon adimi calistirir ve uretilen cerceveleri dondurur."""
        dt = self.settings.tick_seconds if dt is None else dt
        now = time.time()
        self._tick_count += 1
        frames: list[dict] = []

        for vehicle in self.fleet:
            state = self.states[vehicle.id]
            if not state.online:
                # Kapali ECU CAN hattina mesaj basmaz (bus sessizligi).
                state.speed_kmh = 0.0
                state.engine_rpm = 0.0
                state.last_frames = {}
                continue

            self._advance(vehicle, state, dt)
            frames.extend(self._emit(vehicle, state, now))

        return frames

    def _advance(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """Arac dinamigini bir adim ilerletir."""
        ceiling = min(vehicle.max_speed_kmh, self.settings.max_speed_kmh)

        # --- hedef hiz secimi ------------------------------------------ #
        if state.mode == "auto":
            state._auto_timer -= dt
            if state._auto_timer <= 0:
                state._auto_timer = self.settings.auto_retarget_s * self._rng.uniform(0.6, 1.6)
                state.target_speed_kmh = round(self._rng.uniform(0.0, ceiling), 1)
                state.control_source = "speed"
        elif state.mode == "idle":
            state.target_speed_kmh = 0.0

        if state.cruise_active and state.cruise_set_speed_kmh is not None and not state.brake:
            state.target_speed_kmh = float(state.cruise_set_speed_kmh)

        # Pedal kontrolunde hedef, gaz pedalinin yuzdesinden turetilir.
        if state.control_source == "pedal":
            state.target_speed_kmh = ceiling * state.accel_pedal_pct / 100.0

        # --- vites kademesi kisitlari ---------------------------------- #
        if state.gear_range in ("P", "N"):
            target = 0.0
        elif state.gear_range == "R":
            target = min(state.target_speed_kmh, REVERSE_MAX_KMH)
        else:
            target = state.target_speed_kmh

        if state.parking_brake or state.brake:
            target = 0.0

        # --- hiz rampasi ----------------------------------------------- #
        delta = target - state.speed_kmh
        if state.parking_brake:
            step = self.settings.brake_decel_kmh_s * dt
        elif state.brake:
            # Yavaslama pedal basincina gore olceklenir.
            factor = 0.35 + 0.65 * (state.brake_pedal_pct / 100.0)
            step = self.settings.brake_decel_kmh_s * factor * dt
        elif state.gear_range == "N":
            step = self.settings.decel_kmh_s * 0.4 * dt  # bosta serbest yavaslama
        elif delta >= 0:
            step = self.settings.accel_kmh_s * dt
        else:
            step = self.settings.decel_kmh_s * dt

        if abs(delta) <= step:
            state.speed_kmh = target
        else:
            state.speed_kmh += step if delta > 0 else -step

        if state.speed_kmh > 0.5 and self.settings.speed_noise_kmh > 0:
            state.speed_kmh += self._rng.uniform(
                -self.settings.speed_noise_kmh, self.settings.speed_noise_kmh
            )
        state.speed_kmh = min(max(state.speed_kmh, 0.0), self.settings.max_speed_kmh)

        # --- turetilen sinyaller ---------------------------------------- #
        self._update_pedals(state, target)
        self._update_gear(vehicle, state)
        self._update_engine(vehicle, state)
        self._update_battery(vehicle, state, dt)
        self._update_torque(vehicle, state)
        self._update_driveline(vehicle, state, dt)
        self._update_thermal(vehicle, state, dt)
        self._update_pressures(vehicle, state)
        self._update_fuel(vehicle, state, dt)
        self._update_ambient(state, dt)
        self._update_brake_status(state, dt)
        self._update_axles(state)
        self._update_dash(state, dt)
        self._update_position(state, dt)
        self._update_hours_and_distance(state, dt)
        self._update_faults(state)

        state.updated_at = time.time()

    @staticmethod
    def _seed_position(state: VehicleState, vehicle: Vehicle) -> None:
        """Kaynak adresten turetilen deterministik baslangic konumu (Istanbul cevresi)."""
        state.latitude = 41.0 + (vehicle.source_address % 30) * 0.015
        state.longitude = 28.9 + (vehicle.source_address % 30) * 0.02
        state.heading_deg = (vehicle.source_address * 37) % 360

    def _seed_environment(self, state: VehicleState, vehicle: Vehicle) -> None:
        """
        Arac basina baslangic ortam / sarf degerleri.

        Filodaki 30 arac ayni degerlerle baslamasin diye her arac kendi
        ortam sicakligi, depo doluluklari ve calisma saatiyle kurulur; motor
        soguktur (sicakliklar ortam degerinden baslar ve ilk dakikalarda isinir).
        """
        state.gear_count = vehicle.gear_count
        state.ambient_air_temp_c = round(self._rng.uniform(4.0, 32.0), 1)
        state.barometric_pressure_kpa = round(self._rng.uniform(96.0, 103.0), 1)
        state.road_surface_temp_c = state.ambient_air_temp_c + 4.0
        state.air_inlet_temp_c = state.ambient_air_temp_c
        state.cab_interior_temp_c = state.ambient_air_temp_c
        state.cargo_ambient_temp_c = state.ambient_air_temp_c - 8.0

        # Soguk motor: tum sicakliklar ortam degerinden baslar.
        state.coolant_temp_c = state.ambient_air_temp_c
        state.oil_temp_c = state.ambient_air_temp_c
        state.turbo_oil_temp_c = state.ambient_air_temp_c
        state.fuel_temp_c = state.ambient_air_temp_c
        state.intake_manifold_temp_c = state.ambient_air_temp_c
        state.exhaust_gas_temp_c = state.ambient_air_temp_c

        state.fuel_level_pct = round(self._rng.uniform(20.0, 98.0), 1)
        state.fuel_level2_pct = round(self._rng.uniform(20.0, 98.0), 1)
        state.washer_fluid_level_pct = round(self._rng.uniform(35.0, 100.0), 1)
        state.oil_level_pct = round(self._rng.uniform(72.0, 98.0), 1)
        state.coolant_level_pct = round(self._rng.uniform(78.0, 99.0), 1)

        # Filo yasi: her aracin gecmis calisma saati farklidir (odometre 0'dan
        # baslar, cunku trip sayaci onunla birlikte ilerler).
        state.engine_hours = round(self._rng.uniform(400.0, 14_000.0), 2)
        state.pto_hours = round(state.engine_hours * self._rng.uniform(0.01, 0.09), 2)

        # Ceki demiri: kaynak adresten turetilen deterministik dagilim.
        state.trailer_connected = vehicle.source_address % 3 == 0

        ceiling = min(vehicle.max_speed_kmh, self.settings.max_speed_kmh)
        state.cruise_high_limit_kmh = int(min(ceiling, 250))
        state.cruise_low_limit_kmh = 30

    def _update_position(self, state: VehicleState, dt: float) -> None:
        """Hareket halindeyken konumu hafif rastgele bir rota ile ilerletir."""
        if state.speed_kmh <= 0.5:
            return
        state.heading_deg = (state.heading_deg + self._telemetry_rng.uniform(-3.0, 3.0)) % 360.0
        heading_rad = math.radians(state.heading_deg)
        distance_m = state.speed_kmh / 3.6 * dt
        lat_rad = math.radians(state.latitude)
        state.latitude += (distance_m * math.cos(heading_rad)) / 111_320.0
        state.longitude += (distance_m * math.sin(heading_rad)) / (
            111_320.0 * max(math.cos(lat_rad), 1e-6)
        )

    def _update_hours_and_distance(self, state: VehicleState, dt: float) -> None:
        """Motor calisma saati her zaman, mesafe hiza gore birikir."""
        state.engine_hours += dt / 3600.0
        distance_km = (state.speed_kmh * dt) / 3600.0
        state.trip_km += distance_km
        state.odometer_km += distance_km

    def _update_faults(self, state: VehicleState) -> None:
        """Demo amacli, dusuk olasilikli rastgele DM1 ariza tetiklemesi."""
        if len(state.active_dtcs) < 4 and self._telemetry_rng.random() < 0.0001:
            spn, fmi = self._telemetry_rng.choice(FAULT_POOL)
            self._add_fault(state, spn, fmi)

    @staticmethod
    def _add_fault(state: VehicleState, spn: int, fmi: int) -> dict:
        """Ayni SPN/FMI zaten aktifse tekrar sayacini artirir, degilse ekler."""
        for dtc in state.active_dtcs:
            if dtc["spn"] == spn and dtc["fmi"] == fmi:
                dtc["occurrence_count"] += 1
                return dtc
        dtc = {
            "spn": spn,
            "spn_name": SPN_NAMES.get(spn, f"SPN {spn}"),
            "fmi": fmi,
            "fmi_name": FMI_NAMES.get(fmi, f"FMI {fmi}"),
            "occurrence_count": 1,
            "triggered_at": time.time(),
        }
        state.active_dtcs.append(dtc)
        return dtc

    def _update_pedals(self, state: VehicleState, target: float) -> None:
        """Hiz kontrolunde gaz pedali, hiz hatasindan turetilir."""
        if state.control_source == "pedal":
            return

        if state.brake or state.parking_brake or state.gear_range in ("P", "N"):
            state.accel_pedal_pct = 0.0
            return

        error = target - state.speed_kmh
        if error > 0.2:
            demand = 30.0 + min(error * 2.0, 50.0)  # hizlanirken pedal artar
        elif target > 0.5:
            demand = 15.0 + (state.speed_kmh / 180.0) * 30.0  # sabit hizde tutus
        else:
            demand = 0.0

        # Pedal aniden degil, yumusak gecisle hareket eder.
        state.accel_pedal_pct += (demand - state.accel_pedal_pct) * 0.25
        state.accel_pedal_pct = min(max(state.accel_pedal_pct, 0.0), 100.0)

    def _update_gear(self, vehicle: Vehicle, state: VehicleState) -> None:
        """Otomatik sanziman davranisi: hiza gore vites secimi."""
        if not state.gear_auto:
            return

        if state.gear_range in ("P", "N"):
            state.gear = 0
        elif state.gear_range == "R":
            state.gear = -1
        else:
            if state.speed_kmh < 1.0:
                state.gear = 1
            else:
                span = max(vehicle.max_speed_kmh / vehicle.gear_count, 1e-6)
                state.gear = min(int(state.speed_kmh / span) + 1, vehicle.gear_count)

    def _update_engine(self, vehicle: Vehicle, state: VehicleState) -> None:
        """Vites araligina gore testere disi bicimli motor devri."""
        if vehicle.powertrain == "electric":
            ratio = state.speed_kmh / max(vehicle.max_speed_kmh, 1e-6)
            state.engine_rpm = min(ratio * vehicle.max_rpm, vehicle.max_rpm)
        else:
            idle, top = vehicle.idle_rpm, vehicle.max_rpm
            if state.gear > 0 and state.speed_kmh > 0.5:
                span = max(vehicle.max_speed_kmh / vehicle.gear_count, 1e-6)
                in_gear = state.speed_kmh - (state.gear - 1) * span
                frac = min(max(in_gear / span, 0.0), 1.0)
                base = idle + frac * (top - idle)
            else:
                base = idle
            rev = (state.accel_pedal_pct / 100.0) * (top - idle) * 0.15
            state.engine_rpm = min(base + rev, top)

        load = state.accel_pedal_pct * 0.8 + (state.speed_kmh / 180.0) * 20.0
        state.engine_load_pct = min(max(load, 0.0), 125.0)

    def _update_battery(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """SOC tuketimi ve rejeneratif frenleme."""
        if vehicle.powertrain == "diesel":
            # Starter akusu: alternator sarjda tutar, kucuk dalgalanma.
            hedef = 96.0 + (state.engine_rpm / max(vehicle.max_rpm, 1)) * 3.0
            state.soc_pct += (hedef - state.soc_pct) * 0.02
        else:
            power_kw = vehicle.power_hp * 0.7355
            draw_kw = power_kw * (state.engine_load_pct / 100.0)
            if vehicle.powertrain == "hybrid":
                draw_kw *= 0.25
            consumed_kwh = draw_kw * dt / 3600.0

            if state.brake and state.speed_kmh > 1.0:
                # Rejeneratif frenleme: enerjinin bir kismi geri kazanilir.
                consumed_kwh = -0.4 * power_kw * (state.brake_pedal_pct / 100.0) * dt / 3600.0

            state.soc_pct -= consumed_kwh / max(vehicle.battery_kwh, 1e-6) * 100.0

        state.soc_pct = min(max(state.soc_pct, 0.0), 100.0)
        state.soh_pct = min(max(state.soh_pct, 0.0), 100.0)

    # ------------------------------------------------------------------ #
    # DBC sinyalleri: turetilmis fiziksel buyuklukler
    #
    # Asagidaki modeller bilerek basittir: amac gercek bir motor haritasi
    # olusturmak degil, DBC'deki her sinyalin surus durumuyla (hiz, pedal,
    # vites, yuk) tutarli ve gozle izlenebilir sekilde degismesini saglamak.
    # Hicbiri bagimsiz rastgele gurultu degildir.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _approach(current: float, target: float, dt: float, tau: float) -> float:
        """Birinci dereceden gecis: current degerini tau zaman sabitiyle target'a yaklastirir."""
        if tau <= 0:
            return target
        return current + (target - current) * min(dt / tau, 1.0)

    @staticmethod
    def _rpm_fraction(vehicle: Vehicle, state: VehicleState) -> float:
        """Motor devrinin rolanti-azami araligindaki konumu (0..1)."""
        span = max(vehicle.max_rpm - vehicle.idle_rpm, 1.0)
        return min(max((state.engine_rpm - vehicle.idle_rpm) / span, 0.0), 1.0)

    def _update_torque(self, vehicle: Vehicle, state: VehicleState) -> None:
        """EEC1: SPN 512 / 513 / 2432 / 899 / 1675 ve EEC2 SPN 539 / 1481."""
        # Surucu talebi dogrudan gaz pedalindan, gercek tork motor yukunden gelir.
        state.driver_demand_torque_pct = min(state.accel_pedal_pct, 125.0)
        state.actual_engine_torque_pct = min(state.engine_load_pct, 125.0)
        state.demand_engine_torque_pct = state.driver_demand_torque_pct

        # SPN 899 Engine Torque Mode
        if state.cruise_active:
            state.torque_mode = 2  # Cruise Control
        elif state.shift_in_process:
            state.torque_mode = 6  # Transmission
        elif state.accel_pedal_pct > 0.5:
            state.torque_mode = 1  # Pedal
        else:
            state.torque_mode = 0  # Low Idle

        # SPN 1675: arac cevrimici oldugu surece mars tamamlanmis kabul edilir.
        state.starter_mode = 2 if state.engine_rpm > 0 else 0

        rpm_frac = self._rpm_fraction(vehicle, state)
        # Azami tork egrisi: orta devirde tepe yapar, yuksek devirde duser.
        state.max_available_torque_pct = round(100.0 - 18.0 * abs(rpm_frac - 0.45), 1)
        # Parazitik kayiplar devirle artar (fan, pompa, alternator).
        state.parasitic_losses_pct = round(4.0 + rpm_frac * 9.0, 1)

    def _update_driveline(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """ETC1: SPN 191 / 161 / 522 / 560 / 573 / 574."""
        # Vites degistiginde kisa sureli "shift in process" penceresi acilir.
        if state.gear != state._last_gear:
            state._shift_timer = SHIFT_DURATION_S
            state._last_gear = state.gear
        if state._shift_timer > 0:
            state._shift_timer = max(state._shift_timer - dt, 0.0)
        state.shift_in_process = state._shift_timer > 0

        state.driveline_engaged = state.gear_range in ("D", "R") and not state.parking_brake

        ratio = state._gear_ratio() or 0.0
        if ratio > 0 and state.driveline_engaged:
            state.output_shaft_rpm = state.engine_rpm / ratio
        else:
            state.output_shaft_rpm = 0.0

        # Kalkista ve vites degisiminde debriyaj/konvertor kayar; seyirde kilitlenir.
        if state.shift_in_process:
            slip = 35.0
        elif state.driveline_engaged and state.speed_kmh < 15.0:
            slip = max(0.0, 25.0 - state.speed_kmh * 1.5)
        else:
            slip = 0.0
        state.clutch_slip_pct = round(min(slip, 100.0), 1)

        state.torque_converter_lockup = (
            state.driveline_engaged and state.speed_kmh > 30.0 and not state.shift_in_process
        )
        # Giris mili, kayma oraninda motor devrinin altinda doner.
        state.input_shaft_rpm = state.engine_rpm * (1.0 - state.clutch_slip_pct / 100.0)

    def _update_thermal(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """ET1 / IC1: motor, yag, turbo, egzoz ve emme sicakliklari."""
        load = state.engine_load_pct / 100.0
        rpm_frac = self._rpm_fraction(vehicle, state)

        if vehicle.powertrain == "electric":
            # Elektrikli araclarda icten yanma sicakliklari yoktur; sogutma
            # devresi yine de motor/invertor isisini tasir.
            coolant_target = state.ambient_air_temp_c + 12.0 + load * 18.0
            exhaust_target = state.ambient_air_temp_c
        else:
            # Termostat ~85 C'de acar; agir yukte 95 C'ye kadar tirmanir.
            coolant_target = COOLANT_NOMINAL_C + load * 8.0
            exhaust_target = 180.0 + load * 380.0 + rpm_frac * 120.0

        state.coolant_temp_c = self._approach(
            state.coolant_temp_c, coolant_target, dt, COOLANT_TAU_S
        )
        # Yag sogutma suyunu geriden takip eder ve yukte daha sicak olur.
        state.oil_temp_c = self._approach(
            state.oil_temp_c, state.coolant_temp_c + 8.0 + load * 22.0, dt, OIL_TAU_S
        )
        # Turbo yagi egzoz tarafindan ek olarak isitilir.
        state.turbo_oil_temp_c = self._approach(
            state.turbo_oil_temp_c, state.oil_temp_c + 10.0 + rpm_frac * 45.0, dt, OIL_TAU_S
        )
        state.exhaust_gas_temp_c = self._approach(
            state.exhaust_gas_temp_c, exhaust_target, dt, EXHAUST_TAU_S
        )
        state.fuel_temp_c = self._approach(
            state.fuel_temp_c, state.ambient_air_temp_c + 14.0 + load * 12.0, dt, COOLANT_TAU_S
        )
        # Turbo sonrasi emme havasi: intercooler ile ortamin biraz uzerinde tutulur.
        state.intake_manifold_temp_c = self._approach(
            state.intake_manifold_temp_c,
            state.ambient_air_temp_c + 10.0 + state.boost_pressure_kpa * 0.11,
            dt,
            OIL_TAU_S,
        )

    def _update_pressures(self, vehicle: Vehicle, state: VehicleState) -> None:
        """EFLP1 / IC1: yag, yakit, sogutma suyu ve emme/egzoz basinclari."""
        rpm_frac = self._rpm_fraction(vehicle, state)
        load = state.engine_load_pct / 100.0
        running = state.engine_rpm > 1.0

        if vehicle.powertrain == "electric" or not running:
            state.oil_pressure_kpa = 0.0
            state.boost_pressure_kpa = 0.0
            state.fuel_delivery_pressure_kpa = 0.0
        else:
            # Yag basinci devirle dogrusala yakin artar (rolanti ~150, tam ~480 kPa).
            state.oil_pressure_kpa = round(150.0 + rpm_frac * 330.0, 1)
            # Turbo basinci esas olarak yuke baglidir ve devirle sinirlanir.
            state.boost_pressure_kpa = round(load * rpm_frac * 260.0, 1)
            state.fuel_delivery_pressure_kpa = round(360.0 + load * 40.0, 1)

        # Sogutma devresi isindikca basinclanir.
        state.coolant_pressure_kpa = round(max(0.0, (state.coolant_temp_c - 40.0) * 1.6), 1)
        # Partikul filtresi fark basinci egzoz debisiyle artar.
        state.particulate_trap_pressure_kpa = round(1.0 + load * rpm_frac * 5.5, 2)
        # Hava filtresi calisma saatiyle yavasca tikanir (bakim gostergesi).
        state.air_filter_diff_pressure_kpa = round(
            min(0.4 + state.engine_hours * AIR_FILTER_CLOG_KPA_PER_HOUR, 6.0), 2
        )

    def _update_fuel(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """LFE1 / DD: yakit tuketim hizi, ekonomi ve depo seviyeleri."""
        state.throttle_valve_pct = round(state.accel_pedal_pct, 1)

        if vehicle.powertrain == "electric":
            # Elektrikli araclarda yakit tuketimi yoktur: "veri yok" olarak yayinlanir.
            state.fuel_rate_lph = 0.0
            state.instant_fuel_economy_kmpl = 0.0
            return

        power_kw = vehicle.power_hp * 0.7355
        mechanical_kw = power_kw * (state.engine_load_pct / 100.0)
        if vehicle.powertrain == "hybrid":
            mechanical_kw *= 0.6  # elektrikli destek yakit talebini dusurur
        # Rolanti tuketimi + ureteceginden fazla is basina litre (yaklasik BSFC).
        state.fuel_rate_lph = round(IDLE_FUEL_LPH + mechanical_kw * FUEL_LITRE_PER_KWH, 2)

        consumed_l = state.fuel_rate_lph * dt / 3600.0
        state.fuel_used_l += consumed_l

        if state.speed_kmh > 1.0 and state.fuel_rate_lph > 0.01:
            state.instant_fuel_economy_kmpl = round(state.speed_kmh / state.fuel_rate_lph, 3)
        else:
            state.instant_fuel_economy_kmpl = 0.0

        if state.fuel_used_l > 0.001:
            state.average_fuel_economy_kmpl = round(state.trip_km / state.fuel_used_l, 3)

        # Iki depo esit oranda bosalir.
        drop_pct = consumed_l / FUEL_TANK_L * 100.0
        state.fuel_level_pct = max(0.0, state.fuel_level_pct - drop_pct)
        state.fuel_level2_pct = max(0.0, state.fuel_level2_pct - drop_pct)

    def _update_ambient(self, state: VehicleState, dt: float) -> None:
        """AMB / DD: ortam sicakliklari ve barometrik basinc (yavas surunme)."""
        drift = self._telemetry_rng.uniform(-AMBIENT_DRIFT_C, AMBIENT_DRIFT_C) * dt
        state.ambient_air_temp_c = min(max(state.ambient_air_temp_c + drift, -30.0), 50.0)
        # Yol yuzeyi gunes altinda havadan sicaktir; kabin klima ile sabit tutulur.
        state.road_surface_temp_c = self._approach(
            state.road_surface_temp_c, state.ambient_air_temp_c + 6.0, dt, 60.0
        )
        state.air_inlet_temp_c = self._approach(
            state.air_inlet_temp_c, state.ambient_air_temp_c + 2.0, dt, 30.0
        )
        state.cab_interior_temp_c = self._approach(state.cab_interior_temp_c, 22.0, dt, 120.0)
        state.cargo_ambient_temp_c = self._approach(
            state.cargo_ambient_temp_c, state.ambient_air_temp_c - 10.0, dt, 300.0
        )

    def _update_brake_status(self, state: VehicleState, dt: float) -> None:
        """EBC1: ABS / ASR durumlari ve toplam fren talebi."""
        state.total_brake_demand_pct = state.brake_pedal_pct
        state.foundation_brakes_in_use = state.brake

        # Sert frenlemede ABS kisa sureli devreye girer.
        if state.brake_pedal_pct >= ABS_TRIGGER_PEDAL_PCT and state.speed_kmh > ABS_MIN_SPEED_KMH:
            state._abs_timer = ABS_HOLD_S
        if state._abs_timer > 0:
            state._abs_timer = max(state._abs_timer - dt, 0.0)
        state.abs_active = state._abs_timer > 0

        # Sert hizlanmada (yuksek tork, dusuk hiz) ASR cekis kontrolu devreye girer.
        spinning = state.accel_pedal_pct > 80.0 and 0.5 < state.speed_kmh < 25.0
        state.asr_engine_control = spinning
        state.asr_brake_control = spinning
        state.atc_asr_information = spinning or state.abs_active

    def _update_axles(self, state: VehicleState) -> None:
        """EBC2: aks bazli bagil tekerlek hizlari (SPN 905-910)."""
        if state.speed_kmh <= 0.5:
            state.wheel_slip = (0.0,) * 6
            return
        # ABS/ASR sirasinda tekerlekler belirgin sekilde ayrisir.
        spread = (
            WHEEL_SLIP_ABS_KMH if (state.abs_active or state.asr_brake_control) else WHEEL_SLIP_KMH
        )
        state.wheel_slip = tuple(
            round(self._telemetry_rng.uniform(-spread, spread), 4) for _ in range(6)
        )

    def _update_dash(self, state: VehicleState, dt: float) -> None:
        """DD: sivi seviyeleri, kemer, aydinlatma ve bakim lambasi."""
        # Cam suyu yalnizca arac hareket halindeyken cok yavas azalir.
        if state.speed_kmh > 1.0:
            state.washer_fluid_level_pct = max(
                0.0, state.washer_fluid_level_pct - WASHER_USE_PCT_PER_HOUR * dt / 3600.0
            )
        # Yag ve sogutma suyu seviyeleri calisma saatiyle cok yavas duser.
        state.oil_level_pct = max(0.0, state.oil_level_pct - 0.02 * dt / 3600.0)
        state.coolant_level_pct = max(0.0, state.coolant_level_pct - 0.01 * dt / 3600.0)

        state.seat_belt_fastened = state.speed_kmh > 0.5 or state.gear_range != "P"
        state.exterior_light_on = state.speed_kmh > 0.5
        # Bakim lambasi: servis araligi asildiginda veya kritik seviye dustugunde.
        state.maintenance_lamp_on = (
            state.odometer_km % SERVICE_INTERVAL_KM > SERVICE_WARN_KM
            or state.oil_level_pct < 25.0
            or state.coolant_level_pct < 25.0
            or state.air_filter_diff_pressure_kpa > 5.0
        )

        if state.pto_state:
            state.pto_hours += dt / 3600.0

    def _emit(self, vehicle: Vehicle, state: VehicleState, now: float) -> list[dict]:
        """Bu tick'te yayin sirasi gelen mesajlarin cercevelerini uretir."""
        signals = state.signals(source_address=vehicle.source_address)
        produced: list[dict] = []

        for pgn, message in MESSAGES.items():
            if self._tick_count % self._intervals[pgn] != 0:
                continue

            frame = build_frame(pgn, signals, vehicle.source_address, timestamp=now)
            state.tx_counter += 1

            payload = frame.to_dict()
            payload.update(
                {
                    "vehicle_id": vehicle.id,
                    "display_name": vehicle.display_name,
                    "speed_kmh": round(state.speed_kmh, 2),
                    "tx_counter": state.tx_counter,
                    "signals": message.parser(frame.data),
                }
            )
            state.last_frames[pgn] = payload
            produced.append(payload)

        return produced

    # ------------------------------------------------------------------ #
    # Komutlar
    # ------------------------------------------------------------------ #
    def _require(self, vehicle_id: str) -> tuple[Vehicle, VehicleState]:
        vehicle = self.fleet.get(vehicle_id)
        if vehicle is None:
            raise SimulatorError(f"Bilinmeyen arac: {vehicle_id}")
        return vehicle, self.states[vehicle_id]

    def _clamp_speed(self, speed_kmh: float) -> float:
        return min(max(float(speed_kmh), self.settings.min_speed_kmh), self.settings.max_speed_kmh)

    @staticmethod
    def _clamp_pct(value: float) -> float:
        return min(max(float(value), 0.0), 100.0)

    def set_speed(self, vehicle_id: str, speed_kmh: float, *, instant: bool = False) -> dict:
        """Arayuzden gelen hiz enjeksiyonu. instant=True ise rampa atlanir."""
        vehicle, state = self._require(vehicle_id)
        target = self._clamp_speed(speed_kmh)

        state.target_speed_kmh = target
        state.control_source = "speed"
        state.mode = "manual"
        state.brake_pedal_pct = 0.0
        if target > 0:
            state.parking_brake = False
            if state.gear_range in ("P", "N"):
                state.gear_range = "D"
        if instant:
            state.speed_kmh = target
            self._update_gear(vehicle, state)
            self._update_engine(vehicle, state)
        state.updated_at = time.time()
        return state.to_dict()

    def set_accelerator(self, vehicle_id: str, pedal_pct: float) -> dict:
        """Gaz pedali konumu (SPN 91). Hedef hiz pedaldan turetilir."""
        vehicle, state = self._require(vehicle_id)
        state.accel_pedal_pct = self._clamp_pct(pedal_pct)
        state.control_source = "pedal"
        state.mode = "manual"
        state.cruise_active = False
        if state.accel_pedal_pct > 0:
            state.brake_pedal_pct = 0.0
            state.parking_brake = False
            if state.gear_range in ("P", "N"):
                state.gear_range = "D"
        ceiling = min(vehicle.max_speed_kmh, self.settings.max_speed_kmh)
        state.target_speed_kmh = ceiling * state.accel_pedal_pct / 100.0
        state.updated_at = time.time()
        return state.to_dict()

    def set_brake_pedal(self, vehicle_id: str, pedal_pct: float) -> dict:
        """Fren pedali konumu (SPN 521). >0 ise fren anahtari (SPN 597) acilir."""
        _, state = self._require(vehicle_id)
        state.brake_pedal_pct = self._clamp_pct(pedal_pct)
        if state.brake:
            state.cruise_active = False
            state.accel_pedal_pct = 0.0
            state.target_speed_kmh = 0.0
        state.updated_at = time.time()
        return state.to_dict()

    def set_brake(self, vehicle_id: str, brake: bool) -> dict:
        """Fren anahtarini ac/kapa (pedali varsayilan basinca getirir)."""
        return self.set_brake_pedal(vehicle_id, BRAKE_PEDAL_ON_PCT if brake else 0.0)

    def set_gear_range(self, vehicle_id: str, gear_range: str) -> dict:
        """Vites kademesi: P, R, N veya D."""
        vehicle, state = self._require(vehicle_id)
        value = str(gear_range).upper()
        if value not in VALID_RANGES:
            raise SimulatorError(
                f"Gecersiz vites kademesi: {gear_range} (gecerli: {', '.join(VALID_RANGES)})"
            )
        state.gear_range = value
        state.gear_auto = True
        if value in ("P", "N"):
            state.target_speed_kmh = 0.0
            state.accel_pedal_pct = 0.0
            state.cruise_active = False
        self._update_gear(vehicle, state)
        state.updated_at = time.time()
        return state.to_dict()

    def set_gear(self, vehicle_id: str, gear: int) -> dict:
        """Vitesi elle sabitler (otomatik secimi devre disi birakir)."""
        vehicle, state = self._require(vehicle_id)
        value = int(gear)
        if not -1 <= value <= vehicle.gear_count:
            raise SimulatorError(
                f"{vehicle.display_name} icin vites araligi -1..{vehicle.gear_count}: {gear}"
            )
        state.gear = value
        state.gear_auto = False
        state.gear_range = "R" if value < 0 else ("N" if value == 0 else "D")
        state.updated_at = time.time()
        return state.to_dict()

    def set_battery(
        self, vehicle_id: str, soc_pct: float | None = None, soh_pct: float | None = None
    ) -> dict:
        """Batarya doluluk (SPN 5464) ve saglik (SPN 5465) degerlerini yazar."""
        _, state = self._require(vehicle_id)
        if soc_pct is not None:
            state.soc_pct = self._clamp_pct(soc_pct)
        if soh_pct is not None:
            state.soh_pct = self._clamp_pct(soh_pct)
        state.updated_at = time.time()
        return state.to_dict()

    def set_mode(self, vehicle_id: str, mode: Mode) -> dict:
        if mode not in VALID_MODES:
            raise SimulatorError(f"Gecersiz mod: {mode} (gecerli: {', '.join(VALID_MODES)})")
        _, state = self._require(vehicle_id)
        state.mode = mode
        state._auto_timer = 0.0
        if mode == "auto":
            state.control_source = "speed"
        if mode == "idle":
            state.target_speed_kmh = 0.0
            state.cruise_active = False
        return state.to_dict()

    def set_online(self, vehicle_id: str, online: bool) -> dict:
        _, state = self._require(vehicle_id)
        state.online = bool(online)
        if not online:
            state.speed_kmh = 0.0
            state.target_speed_kmh = 0.0
            state.accel_pedal_pct = 0.0
            state.engine_rpm = 0.0
            state.cruise_active = False
            state.last_frames = {}
        return state.to_dict()

    def set_parking_brake(self, vehicle_id: str, engaged: bool) -> dict:
        _, state = self._require(vehicle_id)
        state.parking_brake = bool(engaged)
        if engaged:
            state.cruise_active = False
            state.target_speed_kmh = 0.0
            state.accel_pedal_pct = 0.0
        return state.to_dict()

    def set_cruise(self, vehicle_id: str, active: bool, set_speed_kmh: float | None = None) -> dict:
        _, state = self._require(vehicle_id)
        if active:
            speed = state.speed_kmh if set_speed_kmh is None else set_speed_kmh
            state.cruise_set_speed_kmh = int(round(self._clamp_speed(speed)))
            state.cruise_active = True
            state.control_source = "speed"
            state.brake_pedal_pct = 0.0
        else:
            state.cruise_active = False
        return state.to_dict()

    def trigger_fault(
        self, vehicle_id: str, spn: int | None = None, fmi: int | None = None
    ) -> dict:
        """DM1: aktif ariza ekler (spn/fmi verilmezse FAULT_POOL'dan rastgele secilir)."""
        _, state = self._require(vehicle_id)
        if spn is None or fmi is None:
            spn, fmi = self._telemetry_rng.choice(FAULT_POOL)
        if not 0 <= spn <= 0x7FFFF:
            raise SimulatorError(f"Gecersiz SPN: {spn}")
        if not 0 <= fmi <= 0x1F:
            raise SimulatorError(f"Gecersiz FMI: {fmi}")
        self._add_fault(state, spn, fmi)
        return state.to_dict()

    def clear_faults(self, vehicle_id: str) -> dict:
        """DM1: aracin tum aktif ariza kodlarini temizler."""
        _, state = self._require(vehicle_id)
        state.active_dtcs.clear()
        return state.to_dict()

    def set_trailer(self, vehicle_id: str, connected: bool) -> dict:
        """EBC1 SPN 1836: ceki demiri baglantisi."""
        _, state = self._require(vehicle_id)
        state.trailer_connected = bool(connected)
        state.updated_at = time.time()
        return state.to_dict()

    def set_fuel(
        self, vehicle_id: str, level_pct: float | None = None, level2_pct: float | None = None
    ) -> dict:
        """DD SPN 96 / 38: yakit deposu doluluklari."""
        _, state = self._require(vehicle_id)
        if level_pct is not None:
            state.fuel_level_pct = self._clamp_pct(level_pct)
        if level2_pct is not None:
            state.fuel_level2_pct = self._clamp_pct(level2_pct)
        state.updated_at = time.time()
        return state.to_dict()

    def set_ambient(self, vehicle_id: str, temp_c: float) -> dict:
        """AMB SPN 171: dis ortam sicakligi (diger ortam degerleri buna yaklasir)."""
        _, state = self._require(vehicle_id)
        state.ambient_air_temp_c = min(max(float(temp_c), -40.0), 60.0)
        state.updated_at = time.time()
        return state.to_dict()

    def set_pto(self, vehicle_id: str, pto_state: int) -> dict:
        """CCVS1 SPN 976: PTO governor durumu (0-14)."""
        _, state = self._require(vehicle_id)
        value = int(pto_state)
        if not 0 <= value <= 14:
            raise SimulatorError(f"PTO durumu 0-14 araliginda olmali: {pto_state}")
        state.pto_state = value
        state.updated_at = time.time()
        return state.to_dict()

    def reset_trip(self, vehicle_id: str) -> dict:
        """Trip mesafesini (SPN 917) sifirlar; toplam odometre etkilenmez."""
        _, state = self._require(vehicle_id)
        state.trip_km = 0.0
        return state.to_dict()

    def add_vehicle(self, vehicle: Vehicle) -> dict:
        """Arac Ekle panelinden gelen yeni araci calisma zamani durumuna kaydeder."""
        state = VehicleState(vehicle_id=vehicle.id)
        self._seed_position(state, vehicle)
        self._seed_environment(state, vehicle)
        self.states[vehicle.id] = state
        return state.to_dict()

    def fleet_command(self, action: str, vehicle_ids: Iterable[str] | None = None) -> list[dict]:
        """Filo genelinde toplu komut: stop_all | auto_all | manual_all | resume_all."""
        targets = list(vehicle_ids) if vehicle_ids else list(self.states.keys())
        results: list[dict] = []
        for vehicle_id in targets:
            if action == "stop_all":
                self.set_cruise(vehicle_id, False)
                self.set_speed(vehicle_id, 0.0)
                results.append(self.set_mode(vehicle_id, "idle"))
            elif action == "auto_all":
                self.set_online(vehicle_id, True)
                results.append(self.set_mode(vehicle_id, "auto"))
            elif action == "manual_all":
                results.append(self.set_mode(vehicle_id, "manual"))
            elif action == "resume_all":
                results.append(self.set_online(vehicle_id, True))
            else:
                raise SimulatorError(f"Bilinmeyen filo komutu: {action}")
        return results

    # ------------------------------------------------------------------ #
    # Durum raporu
    # ------------------------------------------------------------------ #
    def state_of(self, vehicle_id: str) -> dict:
        _, state = self._require(vehicle_id)
        return state.to_dict()

    def light_states(self) -> dict:
        """Cerceve detayi olmadan tum araclarin durumu (periyodik senkron icin)."""
        return {vid: s.to_dict(include_frames=False) for vid, s in self.states.items()}

    def snapshot(self) -> dict:
        """Baglanan istemciye gonderilen tam filo durumu."""
        return {
            "brands": self.fleet.grouped_by_brand(),
            "states": {vid: s.to_dict() for vid, s in self.states.items()},
            "stats": self.stats(),
            "meta": self.meta(),
        }

    def meta(self) -> dict:
        return {
            **self.fleet.meta,
            "messages": [m.to_dict() for m in MESSAGES.values()],
            "tick_ms": self.settings.tick_ms,
            "min_speed_kmh": self.settings.min_speed_kmh,
            "max_speed_kmh": self.settings.max_speed_kmh,
            "vehicle_count": len(self.states),
            "gear_ranges": list(VALID_RANGES),
            "modes": list(VALID_MODES),
        }

    def stats(self) -> dict:
        online = sum(1 for s in self.states.values() if s.online)
        moving = sum(1 for s in self.states.values() if s.online and s.speed_kmh > 0.5)
        total_tx = sum(s.tx_counter for s in self.states.values())
        uptime = max(time.time() - self._started_at, 1e-6)

        # Her tick'te yayinlanan cerceve sayisi (bus yuku tahmini)
        per_second = sum(
            1000 / (self.settings.tick_ms * interval) for interval in self._intervals.values()
        )
        return {
            "running": self._running,
            "tick_count": self._tick_count,
            "uptime_s": round(uptime, 1),
            "vehicles_total": len(self.states),
            "vehicles_online": online,
            "vehicles_moving": moving,
            "frames_sent": total_tx,
            "frames_per_second": round(total_tx / uptime, 1),
            "bus_load_fps": round(online * per_second, 1),
            "message_count": len(MESSAGES),
        }
