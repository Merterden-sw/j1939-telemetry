"""
J1939 arac telemetri simulatoru.

Her arac icin bagimsiz bir hiz durumu tutulur; sabit periyotlu (varsayilan
100 ms) bir dongude hizlar hedefe dogru gercekci ivme/yavaslama sinirlariyla
guncellenir ve her arac icin bir PGN 65265 (CCVS1) cercevesi uretilir.

Uretilen cerceveler bir geri cagirma (callback) uzerinden yayin katmanina
(WebSocket) aktarilir; simulatorun agdan haberi yoktur.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from .config import Settings
from .fleet import Fleet, Vehicle
from .j1939 import (
    BIT2_OFF,
    BIT2_ON,
    build_ccvs1_frame,
)

logger = logging.getLogger(__name__)

Mode = str  # "manual" | "auto" | "idle"
VALID_MODES: tuple[Mode, ...] = ("manual", "auto", "idle")

FrameCallback = Callable[[list[dict]], Awaitable[None]]


class SimulatorError(ValueError):
    """Gecersiz simulator komutu."""


@dataclass
class VehicleState:
    """Tek bir aracin calisma zamani durumu."""

    vehicle_id: str
    speed_kmh: float = 0.0
    target_speed_kmh: float = 0.0
    mode: Mode = "manual"
    online: bool = True
    brake: bool = False
    parking_brake: bool = False
    cruise_active: bool = False
    cruise_set_speed_kmh: int | None = None
    tx_counter: int = 0
    last_frame: dict | None = None
    updated_at: float = field(default_factory=time.time)
    _auto_timer: float = 0.0

    def to_dict(self, include_frame: bool = True) -> dict:
        """include_frame=False, periyodik durum senkronu icin hafif surumdur."""
        payload = {
            "vehicle_id": self.vehicle_id,
            "speed_kmh": round(self.speed_kmh, 2),
            "target_speed_kmh": round(self.target_speed_kmh, 2),
            "mode": self.mode,
            "online": self.online,
            "brake": self.brake,
            "parking_brake": self.parking_brake,
            "cruise_active": self.cruise_active,
            "cruise_set_speed_kmh": self.cruise_set_speed_kmh,
            "tx_counter": self.tx_counter,
            "updated_at": self.updated_at,
        }
        if include_frame:
            payload["last_frame"] = self.last_frame
        return payload


class Simulator:
    """Filo genelinde J1939 CCVS1 trafigi ureten asenkron simulator."""

    def __init__(self, fleet: Fleet, settings: Settings) -> None:
        self.fleet = fleet
        self.settings = settings
        self.states: dict[str, VehicleState] = {
            vehicle.id: VehicleState(vehicle_id=vehicle.id) for vehicle in fleet
        }
        self._task: asyncio.Task | None = None
        self._callbacks: list[FrameCallback] = []
        self._running = False
        self._tick_count = 0
        self._started_at = time.time()
        self._rng = random.Random(20260902)

    # ------------------------------------------------------------------ #
    # Yasam dongusu
    # ------------------------------------------------------------------ #
    @property
    def running(self) -> bool:
        return self._running

    def on_frames(self, callback: FrameCallback) -> None:
        """Her tick sonunda uretilen cerceveleri alacak geri cagirma ekler."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = time.time()
        self._task = asyncio.create_task(self._run(), name="j1939-simulator")
        logger.info(
            "Simulator basladi: %d arac, %d ms periyot", len(self.states), self.settings.tick_ms
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
                # Gecikme birikmesini onlemek icin zamani sifirla.
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
                state.last_frame = None
                continue

            self._advance(vehicle, state, dt)
            frame = self._emit(vehicle, state, now)
            frames.append(frame)

        return frames

    def _advance(self, vehicle: Vehicle, state: VehicleState, dt: float) -> None:
        """Hiz durumunu ivme sinirlarina gore hedefe dogru ilerletir."""
        if state.mode == "auto":
            state._auto_timer -= dt
            if state._auto_timer <= 0:
                state._auto_timer = self.settings.auto_retarget_s * self._rng.uniform(0.6, 1.6)
                ceiling = min(vehicle.max_speed_kmh, self.settings.max_speed_kmh)
                state.target_speed_kmh = round(self._rng.uniform(0.0, ceiling), 1)
        elif state.mode == "idle":
            state.target_speed_kmh = 0.0

        if state.cruise_active and state.cruise_set_speed_kmh is not None and not state.brake:
            state.target_speed_kmh = float(state.cruise_set_speed_kmh)

        target = 0.0 if (state.brake or state.parking_brake) else state.target_speed_kmh
        delta = target - state.speed_kmh

        if state.brake or state.parking_brake:
            step = self.settings.brake_decel_kmh_s * dt
        elif delta >= 0:
            step = self.settings.accel_kmh_s * dt
        else:
            step = self.settings.decel_kmh_s * dt

        if abs(delta) <= step:
            state.speed_kmh = target
        else:
            state.speed_kmh += step if delta > 0 else -step

        # Hareket halindeki araca kucuk bir tekerlek sensoru gurultusu ekle.
        if state.speed_kmh > 0.5 and self.settings.speed_noise_kmh > 0:
            state.speed_kmh += self._rng.uniform(
                -self.settings.speed_noise_kmh, self.settings.speed_noise_kmh
            )

        state.speed_kmh = min(max(state.speed_kmh, 0.0), self.settings.max_speed_kmh)
        state.updated_at = time.time()

    def _emit(self, vehicle: Vehicle, state: VehicleState, now: float) -> dict:
        """Aracin guncel durumundan bir CCVS1 cercevesi uretir."""
        frame = build_ccvs1_frame(
            state.speed_kmh,
            source_address=vehicle.source_address,
            priority=vehicle.priority,
            timestamp=now,
            parking_brake=BIT2_ON if state.parking_brake else BIT2_OFF,
            brake_switch=BIT2_ON if state.brake else BIT2_OFF,
            cruise_active=BIT2_ON if state.cruise_active else BIT2_OFF,
            cruise_enable=BIT2_ON if state.cruise_active else BIT2_OFF,
            cruise_set_speed_kmh=state.cruise_set_speed_kmh,
        )
        state.tx_counter += 1

        payload = frame.to_dict()
        payload.update(
            {
                "vehicle_id": vehicle.id,
                "display_name": vehicle.display_name,
                "speed_kmh": round(state.speed_kmh, 2),
                "tx_counter": state.tx_counter,
            }
        )
        state.last_frame = payload
        return payload

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

    def set_speed(self, vehicle_id: str, speed_kmh: float, *, instant: bool = False) -> dict:
        """Arayuzden gelen hiz enjeksiyonu. instant=True ise rampa atlanir."""
        vehicle, state = self._require(vehicle_id)
        target = self._clamp_speed(speed_kmh)

        state.target_speed_kmh = target
        state.mode = "manual"
        state.brake = False
        if target > 0:
            state.parking_brake = False
        if instant:
            state.speed_kmh = target
        state.updated_at = time.time()
        logger.debug("%s -> hedef %.1f km/h", vehicle.display_name, target)
        return state.to_dict()

    def set_mode(self, vehicle_id: str, mode: Mode) -> dict:
        if mode not in VALID_MODES:
            raise SimulatorError(f"Gecersiz mod: {mode} (gecerli: {', '.join(VALID_MODES)})")
        _, state = self._require(vehicle_id)
        state.mode = mode
        state._auto_timer = 0.0
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
            state.cruise_active = False
            state.last_frame = None
        return state.to_dict()

    def set_brake(self, vehicle_id: str, brake: bool) -> dict:
        _, state = self._require(vehicle_id)
        state.brake = bool(brake)
        if brake:
            state.cruise_active = False
            state.target_speed_kmh = 0.0
        return state.to_dict()

    def set_parking_brake(self, vehicle_id: str, engaged: bool) -> dict:
        _, state = self._require(vehicle_id)
        state.parking_brake = bool(engaged)
        if engaged:
            state.cruise_active = False
            state.target_speed_kmh = 0.0
        return state.to_dict()

    def set_cruise(self, vehicle_id: str, active: bool, set_speed_kmh: float | None = None) -> dict:
        _, state = self._require(vehicle_id)
        if active:
            speed = state.speed_kmh if set_speed_kmh is None else set_speed_kmh
            state.cruise_set_speed_kmh = int(round(self._clamp_speed(speed)))
            state.cruise_active = True
            state.brake = False
        else:
            state.cruise_active = False
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
        return {vid: state.to_dict(include_frame=False) for vid, state in self.states.items()}

    def snapshot(self) -> dict:
        """Baglanan istemciye gonderilen tam filo durumu."""
        return {
            "brands": self.fleet.grouped_by_brand(),
            "states": {vid: state.to_dict() for vid, state in self.states.items()},
            "stats": self.stats(),
            "meta": {
                **self.fleet.meta,
                "tick_ms": self.settings.tick_ms,
                "min_speed_kmh": self.settings.min_speed_kmh,
                "max_speed_kmh": self.settings.max_speed_kmh,
                "vehicle_count": len(self.states),
            },
        }

    def stats(self) -> dict:
        online = sum(1 for s in self.states.values() if s.online)
        moving = sum(1 for s in self.states.values() if s.online and s.speed_kmh > 0.5)
        total_tx = sum(s.tx_counter for s in self.states.values())
        uptime = max(time.time() - self._started_at, 1e-6)
        return {
            "running": self._running,
            "tick_count": self._tick_count,
            "uptime_s": round(uptime, 1),
            "vehicles_total": len(self.states),
            "vehicles_online": online,
            "vehicles_moving": moving,
            "frames_sent": total_tx,
            "frames_per_second": round(total_tx / uptime, 1),
            "bus_load_fps": round(online * (1000 / self.settings.tick_ms), 1),
        }
