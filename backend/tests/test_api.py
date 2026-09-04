"""REST ve WebSocket entegrasyon testleri."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def wait_for(ws, message_type: str, limit: int = 80) -> dict:
    """Telemetri akisi arasindan beklenen tipteki ilk mesaji dondurur."""
    for _ in range(limit):
        message = ws.receive_json()
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"'{message_type}' mesaji {limit} mesaj icinde gelmedi")


class TestServiceEndpoints:
    def test_health(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["vehicles"] == 30
        assert body["messages"] == 5

    def test_meta_lists_all_messages(self, client):
        body = client.get("/api/meta").json()
        acronyms = {m["acronym"] for m in body["messages"]}
        assert acronyms == {"CCVS1", "EEC2", "ETC2", "EBC1", "HVBATT"}
        assert body["vehicle_count"] == 30
        assert body["gear_ranges"] == ["P", "R", "N", "D"]

    def test_message_catalog(self, client):
        messages = {m["acronym"]: m for m in client.get("/api/j1939/messages").json()["messages"]}
        assert messages["CCVS1"]["pgn"] == 65265 and messages["CCVS1"]["pgn_hex"] == "0xFEF1"
        assert messages["EEC2"]["pgn"] == 61443 and messages["EEC2"]["pgn_hex"] == "0xF003"
        assert messages["ETC2"]["pgn"] == 61445 and messages["ETC2"]["pgn_hex"] == "0xF005"
        assert messages["EBC1"]["pgn"] == 61441 and messages["EBC1"]["pgn_hex"] == "0xF001"
        assert messages["HVBATT"]["pgn"] == 64923 and messages["HVBATT"]["pgn_hex"] == "0xFD9B"

    def test_vehicle_list_is_grouped_by_brand(self, client):
        body = client.get("/api/vehicles").json()
        assert len(body["brands"]) == 10
        assert sum(len(b["vehicles"]) for b in body["brands"]) == 30

    def test_single_vehicle_carries_powertrain_and_ids(self, client):
        body = client.get("/api/vehicles/volvo-fh16").json()
        vehicle = body["vehicle"]
        assert vehicle["display_name"] == "Volvo Trucks FH16"
        assert vehicle["can_id_hex"] == "18FEF106"
        assert vehicle["powertrain"] in {"diesel", "hybrid", "electric"}
        assert set(vehicle["can_ids"]) == {"CCVS1", "EEC2", "ETC2", "EBC1", "HVBATT"}

    def test_unknown_vehicle_returns_404(self, client):
        assert client.get("/api/vehicles/yok-boyle-bir-arac").status_code == 404

    def test_image_fields_are_exposed(self, client):
        # Gorsel istege baglidir; tanimsizken null doner ve arayuz SVG cizer.
        vehicle = client.get("/api/vehicles/mercedes-benz-actros").json()["vehicle"]
        assert "image" in vehicle and "image_credit" in vehicle
        assert vehicle["image"] is None

    def test_every_vehicle_carries_image_fields(self, client):
        araclar = [v for b in client.get("/api/vehicles").json()["brands"] for v in b["vehicles"]]
        assert len(araclar) == 30
        assert all("image" in v and "image_credit" in v for v in araclar)


class TestSignalInjection:
    def test_speed_injection_returns_frame_preview(self, client):
        body = client.post("/api/vehicles/man-tgx/speed", json={"speed_kmh": 60}).json()
        assert body["state"]["target_speed_kmh"] == 60
        assert body["frame_preview"]["candump"] == "18FEF103#F3003C0000FF1FFF"

    def test_speed_out_of_range_rejected(self, client):
        assert (
            client.post("/api/vehicles/man-tgx/speed", json={"speed_kmh": 900}).status_code == 422
        )

    def test_accelerator_pedal(self, client):
        body = client.post("/api/vehicles/daf-cf/accelerator", json={"pedal_pct": 70}).json()
        assert body["state"]["accel_pedal_pct"] == 70
        assert body["state"]["control_source"] == "pedal"

    def test_brake_pedal_sets_switch(self, client):
        body = client.post("/api/vehicles/daf-cf/brake-pedal", json={"pedal_pct": 55}).json()
        assert body["state"]["brake_pedal_pct"] == 55
        assert body["state"]["brake"] is True

    def test_pedal_out_of_range_rejected(self, client):
        assert (
            client.post("/api/vehicles/daf-cf/accelerator", json={"pedal_pct": 150}).status_code
            == 422
        )

    def test_gear_range(self, client):
        body = client.post("/api/vehicles/scania-r500/gear-range", json={"gear_range": "R"}).json()
        assert body["state"]["gear_range"] == "R"
        assert body["state"]["gear"] == -1

    def test_invalid_gear_range_rejected(self, client):
        assert (
            client.post(
                "/api/vehicles/scania-r500/gear-range", json={"gear_range": "X"}
            ).status_code
            == 422
        )

    def test_manual_gear(self, client):
        body = client.post("/api/vehicles/scania-r500/gear", json={"gear": 7}).json()
        assert body["state"]["gear"] == 7
        assert body["state"]["gear_auto"] is False

    def test_gear_beyond_model_range_rejected(self, client):
        # Isuzu NPR 6 vitesli
        assert client.post("/api/vehicles/isuzu-npr/gear", json={"gear": 14}).status_code == 404

    def test_battery(self, client):
        body = client.post(
            "/api/vehicles/volvo-fm/battery", json={"soc_pct": 33, "soh_pct": 91}
        ).json()
        assert body["state"]["soc_pct"] == 33
        assert body["state"]["soh_pct"] == 91

    def test_battery_requires_a_field(self, client):
        assert client.post("/api/vehicles/volvo-fm/battery", json={}).status_code == 422

    def test_mode_and_toggles(self, client):
        assert (
            client.post("/api/vehicles/daf-lf/mode", json={"mode": "auto"}).json()["state"]["mode"]
            == "auto"
        )
        assert (
            client.post("/api/vehicles/daf-lf/brake", json={"value": True}).json()["state"]["brake"]
            is True
        )
        assert (
            client.post("/api/vehicles/daf-lf/online", json={"value": False}).json()["state"][
                "online"
            ]
            is False
        )
        client.post("/api/vehicles/daf-lf/online", json={"value": True})

    def test_cruise_control(self, client):
        body = client.post(
            "/api/vehicles/scania-r500/cruise", json={"active": True, "set_speed_kmh": 88}
        ).json()
        assert body["state"]["cruise_set_speed_kmh"] == 88

    def test_fleet_command(self, client):
        assert (
            client.post("/api/fleet/command", json={"action": "stop_all"}).json()["affected"] == 30
        )

    def test_invalid_fleet_action_rejected(self, client):
        assert client.post("/api/fleet/command", json={"action": "explode"}).status_code == 422


class TestJ1939Endpoints:
    def test_encode_ccvs1(self, client):
        body = client.post(
            "/api/j1939/encode",
            json={"pgn": 65265, "source_address": 0, "signals": {"speed_kmh": 60}},
        ).json()
        assert body["frame"]["candump"] == "18FEF100#F3003C0000FF1FFF"
        assert body["decoded"]["spn_84_wheel_based_speed_kmh"] == 60.0

    def test_encode_eec2(self, client):
        body = client.post(
            "/api/j1939/encode",
            json={"pgn": 61443, "source_address": 3, "signals": {"accel_pedal_pct": 50}},
        ).json()
        assert body["frame"]["can_id_hex"] == "0CF00303"
        assert body["decoded"]["spn_91_accelerator_pedal_position_1_pct"] == 50.0

    def test_encode_etc2(self, client):
        body = client.post(
            "/api/j1939/encode",
            json={
                "pgn": 61445,
                "signals": {"current_gear": 8, "selected_gear": 8, "current_range": "D"},
            },
        ).json()
        assert body["decoded"]["spn_523_current_gear"] == 8
        assert body["decoded"]["spn_163_current_range"] == "D"

    def test_encode_hvbatt(self, client):
        body = client.post(
            "/api/j1939/encode", json={"pgn": 64923, "signals": {"soc_pct": 78, "soh_pct": 96}}
        ).json()
        assert body["frame"]["can_id_hex"] == "18FD9B00"
        assert body["decoded"]["spn_5464_state_of_charge_pct"] == 78.0
        assert body["decoded"]["spn_5465_state_of_health_pct"] == 96.0

    def test_unsupported_pgn_rejected(self, client):
        assert client.post("/api/j1939/encode", json={"pgn": 12345}).status_code == 400

    def test_decode_identifies_message_and_vehicle(self, client):
        body = client.post("/api/j1939/decode", json={"frame": "18FEF100#F3003C0000FF1FFF"}).json()
        assert body["message"]["acronym"] == "CCVS1"
        assert body["signals"]["spn_84_wheel_based_speed_kmh"] == 60.0
        assert body["vehicle"]["display_name"] == "Mercedes-Benz Actros"

    def test_decode_eec2_frame(self, client):
        body = client.post("/api/j1939/decode", json={"frame": "0CF0030A#FC7D28FFFFFFFFFF"}).json()
        assert body["message"]["acronym"] == "EEC2"
        assert body["header"]["priority"] == 3
        assert body["vehicle"]["display_name"] == "Scania S 730"

    def test_decode_rejects_bad_format(self, client):
        assert (
            client.post(
                "/api/j1939/decode", json={"frame": "ZZZZZZZZ#F3003C0000FF1FFF"}
            ).status_code
            == 400
        )

    def test_decode_rejects_unknown_pgn(self, client):
        assert (
            client.post(
                "/api/j1939/decode", json={"frame": "18FFFF00#0000000000000000"}
            ).status_code
            == 400
        )


class TestWebSocket:
    def test_snapshot_on_connect(self, client):
        with client.websocket_connect("/ws") as ws:
            message = ws.receive_json()
            assert message["type"] == "snapshot"
            assert len(message["brands"]) == 10
            assert len(message["meta"]["messages"]) == 5

    def test_speed_command_over_socket(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "set_speed", "vehicle_id": "volvo-fm", "speed_kmh": 75})
            assert wait_for(ws, "ack")["state"]["target_speed_kmh"] == 75

    def test_new_signal_commands_over_socket(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            for payload, field, expected in [
                ({"type": "set_accelerator", "pedal_pct": 40}, "accel_pedal_pct", 40),
                ({"type": "set_brake_pedal", "pedal_pct": 30}, "brake_pedal_pct", 30),
                ({"type": "set_gear_range", "gear_range": "N"}, "gear_range", "N"),
                ({"type": "set_battery", "soc_pct": 55}, "soc_pct", 55),
            ]:
                ws.send_json({**payload, "vehicle_id": "man-tgm"})
                assert wait_for(ws, "ack")["state"][field] == expected

    def test_compact_row_layout(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            telemetry = wait_for(ws, "telemetry")
            row = telemetry["t"][0]
            # [id, hiz, gaz, fren, vites, kademe, rpm, soc, soh, hex, tx]
            assert len(row) == 11
            assert isinstance(row[0], str) and isinstance(row[1], int | float)
            assert row[5] in {"P", "R", "N", "D"}
            assert len(row[9]) == 16

    def test_subscription_returns_all_pgn_frames(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe", "vehicle_ids": ["volvo-fm"]})
            assert wait_for(ws, "subscribed")["vehicle_ids"] == ["volvo-fm"]

            # HVBATT saniyede bir yayinlandigi ve yavas istemcide kuyruktan
            # mesaj dusebildigi icin butce genis tutulur.
            seen = set()
            for _ in range(400):
                message = ws.receive_json()
                if message.get("type") != "telemetry":
                    continue
                for frame in message.get("frames", []):
                    seen.add(frame["acronym"])
                if len(seen) >= 5:
                    break
            assert seen == {"CCVS1", "EEC2", "ETC2", "EBC1", "HVBATT"}

    def test_frames_carry_decoded_signals(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe", "vehicle_ids": ["man-tgx"]})
            wait_for(ws, "subscribed")
            for _ in range(40):
                message = ws.receive_json()
                if message.get("type") != "telemetry":
                    continue
                ccvs1 = next(
                    (f for f in message.get("frames", []) if f["acronym"] == "CCVS1"), None
                )
                if ccvs1:
                    assert "spn_84_wheel_based_speed_kmh" in ccvs1["signals"]
                    return
            raise AssertionError("CCVS1 cercevesi gelmedi")

    def test_ping_pong(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "ping"})
            assert wait_for(ws, "pong")["type"] == "pong"

    def test_unknown_command_returns_error(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "self_destruct"})
            assert "Bilinmeyen komut" in wait_for(ws, "error")["detail"]
