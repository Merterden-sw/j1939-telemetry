"""
J1939 Telemetri Servisi - FastAPI uygulamasi.

  REST : filo tanimi, arac durumu, hiz enjeksiyonu, cerceve kodlama/cozme
  WS   : /ws uzerinden gercek zamanli telemetri yayini ve komut kanali
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .fleet import load_fleet
from .hub import ClientConnection, Hub
from .j1939 import (
    CCVS1_DLC,
    PGN_CCVS1,
    J1939Error,
    build_ccvs1_frame,
    decode_can_id,
    parse_ccvs1_data,
)
from .models import (
    CruiseCommand,
    DecodeRequest,
    EncodeRequest,
    FleetCommand,
    ModeCommand,
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


async def _broadcast_frames(frames: list[dict]) -> None:
    """
    Simulator geri cagirmasi: her tick'te tum istemcilere telemetri gonderir.

    Bant genisligini dusuk tutmak icin filo geneli sikistirilmis bir dizi olarak
    ("t" alani), yalnizca abone olunan araclar icin tam cerceve detayi gonderilir.
    """
    global _tick_seq
    _tick_seq += 1

    compact = [[f["vehicle_id"], f["speed_kmh"], f["data_hex"], f["tx_counter"]] for f in frames]
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
    logger.info("API hazir | %d arac | PGN %d (CCVS1)", len(fleet), PGN_CCVS1)
    try:
        yield
    finally:
        await simulator.stop()
        await hub.shutdown()


app = FastAPI(
    title="J1939 Telemetri Simulatoru",
    description="PGN 65265 (CCVS1) / SPN 84 tabanli arac hizi simulasyon ve yayin servisi",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "clients": hub.client_count,
        "tick_ms": settings.tick_ms,
    }


@app.get("/api/meta", tags=["servis"])
async def meta() -> dict:
    """J1939 mesaj tanimi ve arayuz sinirlari."""
    return {
        **fleet.meta,
        "tick_ms": settings.tick_ms,
        "min_speed_kmh": settings.min_speed_kmh,
        "max_speed_kmh": settings.max_speed_kmh,
        "vehicle_count": len(fleet),
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
# Komutlar
# --------------------------------------------------------------------------- #


@app.post("/api/vehicles/{vehicle_id}/speed", tags=["komut"])
async def set_speed(
    vehicle_id: str, body: SpeedCommand, vehicle=Depends(get_vehicle_or_404)
) -> dict:
    """Secili araca anlik hiz basar (speed injection)."""
    state = simulator.set_speed(vehicle_id, body.speed_kmh, instant=body.instant)
    preview = build_ccvs1_frame(
        body.speed_kmh, source_address=vehicle.source_address, timestamp=time.time()
    )
    return {"state": state, "frame_preview": preview.to_dict()}


@app.post("/api/vehicles/{vehicle_id}/mode", tags=["komut"])
async def set_mode(vehicle_id: str, body: ModeCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_mode(vehicle_id, body.mode)}


@app.post("/api/vehicles/{vehicle_id}/online", tags=["komut"])
async def set_online(vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_online(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/brake", tags=["komut"])
async def set_brake(vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_brake(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/parking-brake", tags=["komut"])
async def set_parking_brake(
    vehicle_id: str, body: ToggleCommand, _=Depends(get_vehicle_or_404)
) -> dict:
    return {"state": simulator.set_parking_brake(vehicle_id, body.value)}


@app.post("/api/vehicles/{vehicle_id}/cruise", tags=["komut"])
async def set_cruise(vehicle_id: str, body: CruiseCommand, _=Depends(get_vehicle_or_404)) -> dict:
    return {"state": simulator.set_cruise(vehicle_id, body.active, body.set_speed_kmh)}


@app.post("/api/fleet/command", tags=["komut"])
async def fleet_command(body: FleetCommand) -> dict:
    states = simulator.fleet_command(body.action, body.vehicle_ids)
    return {"action": body.action, "affected": len(states)}


# --------------------------------------------------------------------------- #
# J1939 arac uclari
# --------------------------------------------------------------------------- #


@app.post("/api/j1939/encode", tags=["j1939"])
async def encode_frame(body: EncodeRequest) -> dict:
    """Durum degistirmeden verilen hiz icin CCVS1 cercevesi uretir."""
    frame = build_ccvs1_frame(
        body.speed_kmh,
        source_address=body.source_address,
        priority=body.priority,
        timestamp=time.time(),
    )
    return {"frame": frame.to_dict(), "decoded": parse_ccvs1_data(frame.data)}


@app.post("/api/j1939/decode", tags=["j1939"])
async def decode_frame(body: DecodeRequest) -> dict:
    """candump bicimindeki bir cerceveyi (ID#DATA) coozumler."""
    text = body.frame.strip().replace(" ", "")
    if "#" not in text:
        raise HTTPException(
            status_code=400, detail="Bicim 'ID#DATA' olmali, ornek: 18FEF100#F3003C0000FF1FFF"
        )

    id_part, data_part = text.split("#", 1)
    try:
        can_id = int(id_part, 16)
        data = bytes.fromhex(data_part)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Gecersiz hex: {exc}") from exc

    if len(data) != CCVS1_DLC:
        raise HTTPException(status_code=400, detail=f"Veri alani {CCVS1_DLC} byte olmali")

    header = decode_can_id(can_id)
    vehicle = fleet.by_source_address(header["source_address"])
    return {
        "header": header,
        "signals": parse_ccvs1_data(data),
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
        "set_mode": lambda: simulator.set_mode(vehicle_id, message["mode"]),
        "set_online": lambda: simulator.set_online(vehicle_id, bool(message["value"])),
        "set_brake": lambda: simulator.set_brake(vehicle_id, bool(message["value"])),
        "set_parking_brake": lambda: simulator.set_parking_brake(
            vehicle_id, bool(message["value"])
        ),
        "set_cruise": lambda: simulator.set_cruise(
            vehicle_id, bool(message["active"]), message.get("set_speed_kmh")
        ),
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
