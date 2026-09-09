"""
J1939 Telemetri Servisi - FastAPI uygulamasi.

  REST : filo tanimi, arac durumu, sinyal enjeksiyonu, cerceve kodlama/cozme
  WS   : /ws uzerinden gercek zamanli telemetri yayini ve komut kanali
"""

from __future__ import annotations

import base64
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .fleet import build_vehicle, load_fleet
from .hub import ClientConnection, Hub
from .j1939 import (
    DLC,
    FMI_NAMES,
    MESSAGES,
    PGN_CCVS1,
    SPN_NAMES,
    J1939Error,
    build_frame,
    decode_can_id,
    parse_frame,
)
from .models import (
    AddVehicleRequest,
    AmbientCommand,
    BatteryCommand,
    CruiseCommand,
    DecodeRequest,
    EncodeRequest,
    FaultCommand,
    FleetCommand,
    FuelCommand,
    GearCommand,
    GearRangeCommand,
    ModeCommand,
    PedalCommand,
    PtoCommand,
    SpeedCommand,
    ToggleCommand,
)
from .simulator import Simulator, SimulatorError

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("j1939.api")

STATS_EVERY_N_TICKS = 10

fleet = load_fleet(settings.fleet_file)
simulator = Simulator(fleet, settings)
hub = Hub()

_tick_seq = 0


def _compact_rows(vehicle_ids: set[str]) -> list[list]:
    """
    Filo geneli icin sikistirilmis telemetri satirlari.

    Alan sirasi arayuzdeki cozucuyle ayni olmalidir:
        [id, hiz, gaz%, fren%, vites, kademe, rpm, soc%, soh%, ccvs1_hex, tx]
    """
    rows: list[list] = []
    for vehicle_id in vehicle_ids:
        state = simulator.states[vehicle_id]
        ccvs1 = state.last_frames.get(PGN_CCVS1) or {}
        rows.append(
            [
                vehicle_id,
                round(state.speed_kmh, 2),
                round(state.accel_pedal_pct, 1),
                round(state.brake_pedal_pct, 1),
                state.gear,
                state.gear_range,
                round(state.engine_rpm),
                round(state.soc_pct, 1),
                round(state.soh_pct, 1),
                ccvs1.get("data_hex", ""),
                state.tx_counter,
            ]
        )
    return rows


async def _broadcast_frames(frames: list[dict]) -> None:
    """
    Simulator geri cagirmasi: her tick'te tum istemcilere telemetri gonderir.

    Bant genisligini dusuk tutmak icin filo geneli sikistirilmis satirlar
    ("t" alani) olarak, yalnizca abone olunan araclarin tam cerceveleri
    ("frames") gonderilir.
    """
    global _tick_seq
    _tick_seq += 1

    transmitting = {f["vehicle_id"] for f in frames}
    compact = _compact_rows(transmitting)

    # Saniyede bir tum istemcilere durum senkronu gonderilir; boylece bir
    # istemcinin verdigi komut (veya filo komutu) digerlerinde de gorunur.
    sync = _tick_seq % STATS_EVERY_N_TICKS == 0
    stats = simulator.stats() if sync else None
    states = simulator.light_states() if sync else None

    def build(client: ClientConnection) -> dict:
        payload: dict = {
            "type": "telemetry",
            "seq": _tick_seq,
            "ts": time.time(),
            "t": compact,
        }
        if client.subscriptions:
            payload["frames"] = [f for f in frames if f["vehicle_id"] in client.subscriptions]
        if stats is not None:
            payload["stats"] = {**stats, "clients": hub.client_count}
            payload["states"] = states
        return payload

    await hub.broadcast_per_client(build)


@asynccontextmanager
async def lifespan(app: FastAPI):
    simulator.on_frames(_broadcast_frames)
    await simulator.start()
    logger.info(
        "API hazir | %d arac | %d mesaj: %s",
        len(fleet),
        len(MESSAGES),
        ", ".join(m.acronym for m in MESSAGES.values()),
    )
    try:
        yield
    finally:
        await simulator.stop()
        await hub.shutdown()


app = FastAPI(
    title="J1939 Telemetri Simulatoru",
    description=(
        "CCVS1 / EEC2 / ETC2 / EBC1 / HVBATT mesajlarini ureten arac telemetri "
        "simulasyon ve yayin servisi"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Arac Ekle panelinden yuklenen gorseller: backend ve frontend ayri
# container'lar oldugu icin dogrudan dosya sistemi paylasimi mumkun degil;
# bu dizin nginx tarafindan /uploads/ altinda proxy'lenir (bkz. nginx.conf).
UPLOAD_DIR = settings.upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.exception_handler(SimulatorError)
async def _simulator_error_handler(_request, exc: SimulatorError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(J1939Error)
async def _j1939_error_handler(_request, exc: J1939Error):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def get_vehicle_or_404(vehicle_id: str):
    vehicle = fleet.get(vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail=f"Bilinmeyen arac: {vehicle_id}")
    return vehicle


# --------------------------------------------------------------------------- #
# Servis / filo
# --------------------------------------------------------------------------- #


@app.get("/api/health", tags=["servis"])
async def health() -> dict:
    return {
        "status": "ok",
        "simulator_running": simulator.running,
        "vehicles": len(fleet),
        "messages": len(MESSAGES),
        "clients": hub.client_count,
        "tick_ms": settings.tick_ms,
    }


@app.get("/api/meta", tags=["servis"])
async def meta() -> dict:
    """J1939 mesaj tanimlari ve arayuz sinirlari."""
    return simulator.meta()


@app.get("/api/dtc-catalog", tags=["ariza"])
async def dtc_catalog() -> dict:
    """DTC (DM1) panelinde gosterilecek SPN/FMI ad sozlugu."""
    return {
        "spn_names": {str(k): v for k, v in SPN_NAMES.items()},
        "fmi_names": {str(k): v for k, v in FMI_NAMES.items()},
    }


@app.get("/api/stats", tags=["servis"])
async def stats() -> dict:
    return {**simulator.stats(), "clients": hub.client_count}


@app.get("/api/vehicles", tags=["filo"])
async def list_vehicles() -> dict:
    """Marka bazli gruplanmis filo + guncel durumlar (arayuz ilk yuklemesi)."""
    return simulator.snapshot()


@app.get("/api/vehicles/{vehicle_id}", tags=["filo"])
async def get_vehicle(vehicle_id: str) -> dict:
    vehicle = get_vehicle_or_404(vehicle_id)
    return {"vehicle": vehicle.to_dict(), "state": simulator.state_of(vehicle_id)}


# --------------------------------------------------------------------------- #
# Sinyal enjeksiyonu
# --------------------------------------------------------------------------- #


@app.post("/api/vehicles/{vehicle_id}/speed", tags=["komut"])
async def set_speed(
    vehicle_id: str, body: SpeedCommand, vehicle=Depends(get_vehicle_or_404)
) -> dict:
    """Secili araca anlik hiz basar (SPN 84)."""
    state = simulator.set_speed(vehicle_id, body.speed_kmh, instant=body.instant)
    preview = build_frame(
        PGN_CCVS1, {"speed_kmh": body.speed_kmh}, vehicle.source_address, timestamp=time.time()
    )
    return {"state": state, "frame_preview": preview.to_dict()}


@app.post("/api/vehicles/{vehicle_id}/accelerator", tags=["komut"])
async def set_accelerator(
    vehicle_id: str, body: PedalCommand, _=Depends(get_vehicle_or_404)
) -> dict:
    """Gaz pedali konumu (SPN 91 / EEC2)."""
    return {"state": simulator.set_accelerator(vehicle_id, body.pedal_pct)}


@app.post("/api/vehicles/{vehicle_id}/brake-pedal", tags=["komut"])
async def set_brake_pedal(
    vehicle_id: str, body: PedalCommand, _=Depends(get_vehicle_or_404)
) -> dict:
    """Fren pedali konumu (SPN 521 / EBC1)."""
    return {"state": simulator.set_brake_pedal(vehicle_id, body.pedal_pct)}


@app.post("/api/vehicles/{vehicle_id}/gear-range", tags=["komut"])
async def set_gear_range(
    vehicle_id: str, body: GearRangeCommand, _=Depends(get_vehicle_or_404)
) -> dict:
    """Vites kademesi P / R / N / D (SPN 162 / 163)."""
    return {"state": simulator.set_gear_range(vehicle_id, body.gear_range)}


@app.post("/api/vehicles/{vehicle_id}/gear", tags=["komut"])
async def set_gear(vehicle_id: str, body: GearCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Vitesi elle sabitler (SPN 523 / 524)."""
    return {"state": simulator.set_gear(vehicle_id, body.gear)}


@app.post("/api/vehicles/{vehicle_id}/battery", tags=["komut"])
async def set_battery(vehicle_id: str, body: BatteryCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Batarya SOC (SPN 5464) ve SOH (SPN 5465)."""
    return {"state": simulator.set_battery(vehicle_id, body.soc_pct, body.soh_pct)}


@app.post("/api/vehicles/{vehicle_id}/mode", tags=["komut"])
async def set_mode(vehicle_id: str, body: ModeCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_mode(vehicle_id, body.mode)}


@app.post("/api/vehicles/{vehicle_id}/online", tags=["komut"])
async def set_online(vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_online(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/brake", tags=["komut"])
async def set_brake(vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Fren anahtarini ac/kapa (SPN 597)."""
    return {"state": simulator.set_brake(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/parking-brake", tags=["komut"])
async def set_parking_brake(
    vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)
) -> dict:
    return {"state": simulator.set_parking_brake(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/cruise", tags=["komut"])
async def set_cruise(vehicle_id: str, body: CruiseCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_cruise(vehicle_id, body.active, body.set_speed_kmh)}


@app.post("/api/vehicles/{vehicle_id}/trailer", tags=["komut"])
async def set_trailer(vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Ceki demiri baglantisi (SPN 1836 / EBC1)."""
    return {"state": simulator.set_trailer(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/fuel", tags=["komut"])
async def set_fuel(vehicle_id: str, body: FuelCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Yakit deposu doluluklari (SPN 96 / 38 / DD)."""
    return {"state": simulator.set_fuel(vehicle_id, body.fuel_level_pct, body.fuel_level2_pct)}


@app.post("/api/vehicles/{vehicle_id}/ambient", tags=["komut"])
async def set_ambient(vehicle_id: str, body: AmbientCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """Dis ortam sicakligi (SPN 171 / AMB)."""
    return {"state": simulator.set_ambient(vehicle_id, body.ambient_air_temp_c)}


@app.post("/api/vehicles/{vehicle_id}/pto", tags=["komut"])
async def set_pto(vehicle_id: str, body: PtoCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """PTO governor durumu (SPN 976 / CCVS1)."""
    return {"state": simulator.set_pto(vehicle_id, body.pto_state)}


@app.post("/api/fleet/command", tags=["komut"])
async def fleet_command(body: FleetCommand) -> dict:
    states = simulator.fleet_command(body.action, body.vehicle_ids)
    return {"action": body.action, "affected": len(states)}


@app.post("/api/vehicles/{vehicle_id}/fault", tags=["ariza"])
async def trigger_fault(vehicle_id: str, body: FaultCommand, _=Depends(get_vehicle_or_404)) -> dict:
    """DM1: ariza tetikler (govde bossa rastgele SPN/FMI secilir)."""
    return {"state": simulator.trigger_fault(vehicle_id, body.spn, body.fmi)}


@app.delete("/api/vehicles/{vehicle_id}/fault", tags=["ariza"])
async def clear_faults(vehicle_id: str, _=Depends(get_vehicle_or_404)) -> dict:
    """DM1: aracin tum aktif ariza kodlarini temizler."""
    return {"state": simulator.clear_faults(vehicle_id)}


@app.post("/api/vehicles/{vehicle_id}/trip/reset", tags=["filo"])
async def reset_trip(vehicle_id: str, _=Depends(get_vehicle_or_404)) -> dict:
    """SPN 917 trip mesafesini sifirlar (toplam odometre etkilenmez)."""
    return {"state": simulator.reset_trip(vehicle_id)}


@app.post("/api/fleet/vehicles", tags=["filo"], status_code=201)
async def add_vehicle(body: AddVehicleRequest) -> dict:
    """Arac Ekle panelinden gelen yeni araci filoya calisma zamaninda ekler."""
    source_address = fleet.next_source_address()
    vehicle = build_vehicle(
        brand_id=body.brand_id,
        brand=body.brand,
        model_id=body.model_id,
        model=body.model,
        segment=body.segment,
        country=body.country,
        color=body.color,
        source_address=source_address,
        max_speed_kmh=body.max_speed_kmh,
        power_hp=body.power_hp,
        powertrain=body.powertrain,
        gear_count=body.gear_count,
        battery_kwh=body.battery_kwh,
    )
    try:
        fleet.add_vehicle(vehicle)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    state = simulator.add_vehicle(vehicle)

    if body.image_data_url and body.image_data_url.startswith("data:image/"):
        try:
            _, encoded = body.image_data_url.split(",", 1)
            (UPLOAD_DIR / f"{vehicle.id}.jpg").write_bytes(base64.b64decode(encoded))
            vehicle = build_vehicle(
                brand_id=body.brand_id,
                brand=body.brand,
                model_id=body.model_id,
                model=body.model,
                segment=body.segment,
                country=body.country,
                color=body.color,
                source_address=source_address,
                max_speed_kmh=body.max_speed_kmh,
                power_hp=body.power_hp,
                powertrain=body.powertrain,
                gear_count=body.gear_count,
                battery_kwh=body.battery_kwh,
                image=f"uploads/{vehicle.id}.jpg",
            )
            fleet.replace_vehicle(vehicle)
        except Exception:
            logger.exception("Arac gorseli kaydedilemedi: %s", vehicle.id)

    await hub.broadcast({"type": "snapshot", **simulator.snapshot()})
    return {"vehicle": vehicle.to_dict(), "state": state}


# --------------------------------------------------------------------------- #
# J1939 arac uclari
# --------------------------------------------------------------------------- #


@app.get("/api/j1939/messages", tags=["j1939"])
async def list_messages() -> dict:
    """Desteklenen PGN'ler, oncelikleri, yayin periyotlari ve SPN listeleri."""
    return {"messages": [m.to_dict() for m in MESSAGES.values()]}


@app.post("/api/j1939/encode", tags=["j1939"])
async def encode_frame(body: EncodeRequest) -> dict:
    """Durum degistirmeden verilen sinyaller icin cerceve uretir."""
    if body.pgn not in MESSAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Desteklenmeyen PGN: {body.pgn} "
                f"(gecerli: {', '.join(str(p) for p in MESSAGES)})"
            ),
        )
    frame = build_frame(
        body.pgn,
        body.signals,
        body.source_address,
        priority=body.priority,
        timestamp=time.time(),
    )
    return {"frame": frame.to_dict(), "decoded": parse_frame(body.pgn, frame.data)}


@app.post("/api/j1939/decode", tags=["j1939"])
async def decode_frame(body: DecodeRequest) -> dict:
    """candump bicimindeki bir cerceveyi (ID#DATA) cozumler."""
    text = body.frame.strip().replace(" ", "")
    if "#" not in text:
        raise HTTPException(
            status_code=400,
            detail="Bicim 'ID#DATA' olmali, ornek: 18FEF100#F3003C0000FF1FFF",
        )

    id_part, data_part = text.split("#", 1)
    try:
        can_id = int(id_part, 16)
        data = bytes.fromhex(data_part)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Gecersiz hex: {exc}") from exc

    if len(data) != DLC:
        raise HTTPException(status_code=400, detail=f"Veri alani {DLC} byte olmali")

    header = decode_can_id(can_id)
    message = MESSAGES.get(header["pgn"])
    if message is None:
        raise HTTPException(
            status_code=400, detail=f"Tanimsiz PGN: {header['pgn']} ({header['pgn_hex']})"
        )

    vehicle = fleet.by_source_address(header["source_address"])
    return {
        "header": header,
        "message": message.to_dict(),
        "signals": message.parser(data),
        "vehicle": vehicle.to_dict() if vehicle else None,
    }


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _handle_ws_command(client: ClientConnection, message: dict) -> dict:
    """Istemciden gelen komutu isler ve yanit sozlugu dondurur."""
    kind = message.get("type")
    vehicle_id = message.get("vehicle_id")

    if kind == "ping":
        return {"type": "pong", "ts": time.time()}

    if kind == "subscribe":
        ids = message.get("vehicle_ids") or []
        client.subscriptions = {vid for vid in ids if fleet.get(vid) is not None}
        return {"type": "subscribed", "vehicle_ids": sorted(client.subscriptions)}

    if kind == "snapshot":
        return {"type": "snapshot", **simulator.snapshot()}

    if kind == "fleet_command":
        simulator.fleet_command(message["action"], message.get("vehicle_ids"))
        return {"type": "ack", "command": kind, "action": message["action"]}

    handlers = {
        "set_speed": lambda: simulator.set_speed(
            vehicle_id, float(message["speed_kmh"]), instant=bool(message.get("instant", False))
        ),
        "set_accelerator": lambda: simulator.set_accelerator(
            vehicle_id, float(message["pedal_pct"])
        ),
        "set_brake_pedal": lambda: simulator.set_brake_pedal(
            vehicle_id, float(message["pedal_pct"])
        ),
        "set_gear_range": lambda: simulator.set_gear_range(vehicle_id, message["gear_range"]),
        "set_gear": lambda: simulator.set_gear(vehicle_id, int(message["gear"])),
        "set_battery": lambda: simulator.set_battery(
            vehicle_id, message.get("soc_pct"), message.get("soh_pct")
        ),
        "set_mode": lambda: simulator.set_mode(vehicle_id, message["mode"]),
        "set_online": lambda: simulator.set_online(vehicle_id, bool(message["value"])),
        "set_brake": lambda: simulator.set_brake(vehicle_id, bool(message["value"])),
        "set_parking_brake": lambda: simulator.set_parking_brake(
            vehicle_id, bool(message["value"])
        ),
        "set_cruise": lambda: simulator.set_cruise(
            vehicle_id, bool(message["active"]), message.get("set_speed_kmh")
        ),
        "trigger_fault": lambda: simulator.trigger_fault(
            vehicle_id, message.get("spn"), message.get("fmi")
        ),
        "set_trailer": lambda: simulator.set_trailer(vehicle_id, bool(message["value"])),
        "set_fuel": lambda: simulator.set_fuel(
            vehicle_id, message.get("fuel_level_pct"), message.get("fuel_level2_pct")
        ),
        "set_ambient": lambda: simulator.set_ambient(
            vehicle_id, float(message["ambient_air_temp_c"])
        ),
        "set_pto": lambda: simulator.set_pto(vehicle_id, int(message["pto_state"])),
        "clear_faults": lambda: simulator.clear_faults(vehicle_id),
        "reset_trip": lambda: simulator.reset_trip(vehicle_id),
    }

    handler = handlers.get(kind)
    if handler is None:
        return {"type": "error", "detail": f"Bilinmeyen komut: {kind}"}

    return {"type": "ack", "command": kind, "state": handler()}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    client = await hub.connect(websocket)
    try:
        await hub.send(client, {"type": "snapshot", **simulator.snapshot()})
        while True:
            message = await websocket.receive_json()
            try:
                response = await _handle_ws_command(client, message)
            except (SimulatorError, J1939Error) as exc:
                response = {"type": "error", "detail": str(exc)}
            except (KeyError, TypeError, ValueError) as exc:
                response = {"type": "error", "detail": f"Gecersiz komut yuku: {exc}"}
            await hub.send(client, response)
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        logger.exception("WebSocket oturum hatasi")
    finally:
        await hub.disconnect(client)
