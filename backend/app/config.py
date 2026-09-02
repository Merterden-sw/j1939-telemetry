"""Ortam degiskenlerinden okunan servis yapilandirmasi."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    """Calisma zamani ayarlari."""

    # Simulasyon
    tick_ms: int = _env_int("SIM_TICK_MS", 100)  # J1939 CCVS1 varsayilan: 100 ms
    accel_kmh_s: float = _env_float("SIM_ACCEL_KMH_S", 5.0)
    decel_kmh_s: float = _env_float("SIM_DECEL_KMH_S", 7.0)
    brake_decel_kmh_s: float = _env_float("SIM_BRAKE_DECEL_KMH_S", 14.0)
    speed_noise_kmh: float = _env_float("SIM_SPEED_NOISE_KMH", 0.15)
    auto_retarget_s: float = _env_float("SIM_AUTO_RETARGET_S", 6.0)

    # Arayuz sinirlari
    min_speed_kmh: float = _env_float("MIN_SPEED_KMH", 0.0)
    max_speed_kmh: float = _env_float("MAX_SPEED_KMH", 180.0)

    # Servis
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = _env_int("PORT", 8000)
    log_level: str = os.getenv("LOG_LEVEL", "info")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    fleet_file: Path = Path(os.getenv("FLEET_FILE", str(BASE_DIR / "data" / "vehicles.json")))
    log_buffer_size: int = _env_int("LOG_BUFFER_SIZE", 500)

    @property
    def tick_seconds(self) -> float:
        return self.tick_ms / 1000.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
