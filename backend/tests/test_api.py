"""REST ve WebSocket entegrasyon testleri."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestServiceEndpoints:
    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["vehicles"] == 30

    def test_meta_exposes_j1939_definition(self, client):
        body = client.get("/api/meta").json()
        assert body["pgn"] == 65265
        assert body["acronym"] == "CCVS1"
        assert body["spns"]["84"]["resolution_text"] == "1/256 km/h per bit"

    def test_vehicle_list_is_grouped_by_brand(self, client):
        body = client.get("/api/vehicles").json()
        assert len(body["brands"]) == 10
        assert sum(len(b["vehicles"]) for b in body["brands"]) == 30

    def test_single_vehicle(self, client):
        body = client.get("/api/vehicles/volvo-fh16").json()
        assert body["vehicle"]["display_name"] == "Volvo Trucks FH16"
        assert body["vehicle"]["can_id_hex"] == "18FEF106"

    def test_unknown_vehicle_returns_404(self, client):
        assert client.get("/api/vehicles/yok-boyle-bir-arac").status_code == 404


class TestCommandEndpoints:
    def test_speed_injection_returns_frame_preview(self, client):
        response = client.post("/api/vehicles/man-tgx/speed", json={"speed_kmh": 60})
        assert response.status_code == 200
        body = response.json()
        assert body["state"]["target_speed_kmh"] == 60
        assert body["frame_preview"]["candump"] == "18FEF103#F3003C0000FF1FFF"

    def test_speed_out_of_range_rejected(self, client):
        assert (
            client.post("/api/vehicles/man-tgx/speed", json={"speed_kmh": 900}).status_code == 422
        )

    def test_mode_and_toggles(self, client):
        assert (
            client.post("/api/vehicles/daf-cf/mode", json={"mode": "auto"}).json()["state"]["mode"]
            == "auto"
        )
        assert (
            client.post("/api/vehicles/daf-cf/brake", json={"value": True}).json()["state"]["brake"]
            is True
        )
        assert (
            client.post("/api/vehicles/daf-cf/online", json={"value": False}).json()["state"][
                "online"
            ]
            is False
        )
        client.post("/api/vehicles/daf-cf/online", json={"value": True})

    def test_cruise_control(self, client):
        body = client.post(
            "/api/vehicles/scania-r500/cruise", json={"active": True, "set_speed_kmh": 88}
        ).json()
        assert body["state"]["cruise_set_speed_kmh"] == 88

    def test_fleet_command(self, client):
        body = client.post("/api/fleet/command", json={"action": "stop_all"}).json()
        assert body["affected"] == 30

    def test_invalid_fleet_action_rejected(self, client):
        assert client.post("/api/fleet/command", json={"action": "explode"}).status_code == 422


class TestJ1939Endpoints:
    def test_encode(self, client):
        body = client.post("/api/j1939/encode", json={"speed_kmh": 60, "source_address": 0}).json()
        assert body["frame"]["can_id_hex"] == "18FEF100"
        assert body["decoded"]["spn_84_wheel_based_speed_kmh"] == 60.0

    def test_decode(self, client):
        body = client.post("/api/j1939/decode", json={"frame": "18FEF100#F3003C0000FF1FFF"}).json()
        assert body["header"]["pgn"] == 65265
        assert body["signals"]["spn_84_wheel_based_speed_kmh"] == 60.0
        assert body["vehicle"]["display_name"] == "Mercedes-Benz Actros"

    def test_decode_rejects_bad_format(self, client):
        assert (
            client.post(
                "/api/j1939/decode", json={"frame": "ZZZZZZZZ#F3003C0000FF1FFF"}
            ).status_code
            == 400
        )


class TestWebSocket:
    def test_snapshot_on_connect(self, client):
        with client.websocket_connect("/ws") as ws:
            message = ws.receive_json()
            assert message["type"] == "snapshot"
            assert len(message["brands"]) == 10
            assert message["meta"]["pgn"] == 65265

    def test_speed_command_over_socket(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # snapshot
            ws.send_json({"type": "set_speed", "vehicle_id": "volvo-fm", "speed_kmh": 75})
            message = _wait_for(ws, "ack")
            assert message["state"]["target_speed_kmh"] == 75

    def test_subscription_returns_detailed_frames(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe", "vehicle_ids": ["volvo-fm", "gecersiz"]})
            assert _wait_for(ws, "subscribed")["vehicle_ids"] == ["volvo-fm"]

            telemetry = _wait_for(ws, "telemetry")
            assert len(telemetry["t"]) >= 1
            if "frames" in telemetry:
                assert telemetry["frames"][0]["candump"].startswith("18FEF107#")

    def test_ping_pong(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "ping"})
            assert _wait_for(ws, "pong")["type"] == "pong"

    def test_unknown_command_returns_error(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "self_destruct"})
            assert "Bilinmeyen komut" in _wait_for(ws, "error")["detail"]


def _wait_for(ws, message_type: str, limit: int = 60) -> dict:
    """Telemetri akisi arasindan beklenen tipteki ilk mesaji dondurur."""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"'{message_type}' mesaji {limit} mesaj icinde gelmedi")
