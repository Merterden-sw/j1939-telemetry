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

    # Motor
    engine_rpm: float = 0.0
    engine_load_pct: float = 0.0

    # Batarya
    soc_pct: float = 85.0
    soh_pct: float = 98.0

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

    def signals(self) -> dict:
        """Mesaj kuruculara verilecek tam sinyal sozlugu."""
        return {
            # CCVS1
            "speed_kmh": self.speed_kmh,
            "parking_brake": self.parking_brake,
            "brake_switch": self.brake,
            "cruise_active": self.cruise_active,
            "cruise_set_speed_kmh": self.cruise_set_speed_kmh,
            # EEC2
            "accel_pedal_pct": self.accel_pedal_pct,
            "engine_load_pct": self.engine_load_pct,
            "remote_accel_pct": None,
            "accel_pedal2_pct": None,
            "kickdown": self.accel_pedal_pct > 95,
            # ETC2
            "current_gear": self.gear,
            "selected_gear": self.gear,
            "gear_ratio": self._gear_ratio(),
            "current_range": self.gear_range,
            "requested_range": self.gear_range,
            # EBC1
            "brake_pedal_pct": self.brake_pedal_pct,
            "abs_active": False,
            "ebs_brake_switch": self.brake,
            "asr_engine_control": False,
            "asr_brake_control": False,
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
            # VDHR
            "trip_km": self.trip_km,
            "total_km": self.odometer_km,
        }

    def _gear_ratio(self) -> float | None:
        """Kaba bir aktarma orani (SPN 526) - gorsel/log amacli."""
        if self.gear <= 0:
            return 0.0
        return round(max(0.5, 6.0 / self.gear), 3)

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

    def _emit(self, vehicle: Vehicle, state: VehicleState, now: float) -> list[dict]:
        """Bu tick'te yayin sirasi gelen mesajlarin cercevelerini uretir."""
        signals = state.signals()
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

    def reset_trip(self, vehicle_id: str) -> dict:
        """Trip mesafesini (SPN 917) sifirlar; toplam odometre etkilenmez."""
        _, state = self._require(vehicle_id)
        state.trip_km = 0.0
        return state.to_dict()

    def add_vehicle(self, vehicle: Vehicle) -> dict:
        """Arac Ekle panelinden gelen yeni araci calisma zamani durumuna kaydeder."""
        state = VehicleState(vehicle_id=vehicle.id)
        self._seed_position(state, vehicle)
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
