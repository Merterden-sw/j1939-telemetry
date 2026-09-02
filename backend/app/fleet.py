"""Filo tanimi: vehicles.json dosyasinin okunmasi ve arac nesnelerine donusturulmesi."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .j1939 import DEFAULT_PRIORITY, PGN_CCVS1, build_can_id, format_can_id


@dataclass(frozen=True)
class Vehicle:
    """Statik arac tanimi (calisma zamani durumu SimulatorState icinde tutulur)."""

    id: str
    brand_id: str
    brand: str
    model_id: str
    model: str
    segment: str
    country: str
    color: str
    source_address: int
    max_speed_kmh: float
    power_hp: int
    can_id: int
    can_id_hex: str
    pgn: int = PGN_CCVS1
    priority: int = DEFAULT_PRIORITY

    @property
    def display_name(self) -> str:
        return f"{self.brand} {self.model}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["display_name"] = self.display_name
        data["source_address_hex"] = f"0x{self.source_address:02X}"
        return data


class Fleet:
    """Araclara id ve kaynak adres uzerinden erisim saglayan salt-okunur kayit."""

    def __init__(self, vehicles: list[Vehicle], meta: dict) -> None:
        self._vehicles = vehicles
        self._by_id = {v.id: v for v in vehicles}
        self._by_sa = {v.source_address: v for v in vehicles}
        self.meta = meta

    # -- erisim ------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._vehicles)

    def __iter__(self):
        return iter(self._vehicles)

    @property
    def vehicles(self) -> list[Vehicle]:
        return list(self._vehicles)

    def get(self, vehicle_id: str) -> Vehicle | None:
        return self._by_id.get(vehicle_id)

    def by_source_address(self, source_address: int) -> Vehicle | None:
        return self._by_sa.get(source_address)

    def grouped_by_brand(self) -> list[dict]:
        """Arayuzun marka bazli kart gridini kurmasi icin gruplanmis yapi."""
        groups: dict[str, dict] = {}
        for vehicle in self._vehicles:
            group = groups.setdefault(
                vehicle.brand_id,
                {
                    "id": vehicle.brand_id,
                    "name": vehicle.brand,
                    "country": vehicle.country,
                    "color": vehicle.color,
                    "vehicles": [],
                },
            )
            group["vehicles"].append(vehicle.to_dict())
        return list(groups.values())


def load_fleet(path: str | Path) -> Fleet:
    """vehicles.json dosyasini okuyup dogrulayarak Fleet nesnesi uretir."""
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    vehicles: list[Vehicle] = []
    seen_addresses: set[int] = set()

    for brand in raw["brands"]:
        for model in brand["models"]:
            source_address = int(model["source_address"])
            if source_address in seen_addresses:
                raise ValueError(
                    f"Yinelenen J1939 kaynak adresi: 0x{source_address:02X} "
                    f"({brand['name']} {model['name']})"
                )
            seen_addresses.add(source_address)

            can_id = build_can_id(PGN_CCVS1, source_address=source_address)
            vehicles.append(
                Vehicle(
                    id=f"{brand['id']}-{model['id']}",
                    brand_id=brand["id"],
                    brand=brand["name"],
                    model_id=model["id"],
                    model=model["name"],
                    segment=model.get("segment", "-"),
                    country=brand.get("country", "-"),
                    color=brand.get("color", "#7d8590"),
                    source_address=source_address,
                    max_speed_kmh=float(model["max_speed_kmh"]),
                    power_hp=int(model.get("power_hp", 0)),
                    can_id=can_id,
                    can_id_hex=format_can_id(can_id),
                )
            )

    if not vehicles:
        raise ValueError("Filo tanimi bos: en az bir arac gerekli")

    return Fleet(vehicles, meta=raw.get("j1939", {}))
