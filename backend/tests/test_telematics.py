"""Filo telematik ozellikleri: DM1 ariza tetikleme, konum, mesafe, arac ekleme."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.fleet import build_vehicle, load_fleet
from app.main import app
from app.simulator import Simulator, SimulatorError


@pytest.fixture
def sim() -> Simulator:
    return Simulator(load_fleet(settings.fleet_file), settings)


def run_ticks(sim: Simulator, count: int) -> list[dict]:
    frames: list[dict] = []
    for _ in range(count):
        frames = sim.tick()
    return frames


class TestSimulatorTelematics:
    def test_position_drifts_while_moving(self, sim):
        start = sim.state_of("man-tgx")
        sim.set_speed("man-tgx", 80, instant=True)
        run_ticks(sim, 20)
        moved = sim.state_of("man-tgx")
        assert (moved["latitude"], moved["longitude"]) != (start["latitude"], start["longitude"])

    def test_odometer_and_engine_hours_increase_when_online(self, sim):
        sim.set_speed("man-tgx", 60, instant=True)
        run_ticks(sim, 20)
        state = sim.state_of("man-tgx")
        assert state["odometer_km"] > 0
        assert state["trip_km"] == pytest.approx(state["odometer_km"])
        assert state["engine_hours"] >= 0

    def test_trigger_fault_appears_in_active_dtcs(self, sim):
        state = sim.trigger_fault("man-tgx", spn=110, fmi=0)
        assert state["active_dtcs"][0]["spn"] == 110
        assert state["active_dtcs"][0]["fmi"] == 0
        frames = run_ticks(sim, 10)  # DM1 her 10 tick'te bir yayinlanir
        frame = next(f for f in frames if f["vehicle_id"] == "man-tgx" and f["acronym"] == "DM1")
        assert frame["signals"]["spn"] == 110

    def test_trigger_fault_random_when_unspecified(self, sim):
        state = sim.trigger_fault("man-tgx")
        assert len(state["active_dtcs"]) == 1

    def test_repeated_same_fault_bumps_occurrence_count(self, sim):
        sim.trigger_fault("man-tgx", spn=110, fmi=0)
        state = sim.trigger_fault("man-tgx", spn=110, fmi=0)
        assert len(state["active_dtcs"]) == 1
        assert state["active_dtcs"][0]["occurrence_count"] == 2

    def test_clear_faults_empties_list(self, sim):
        sim.trigger_fault("man-tgx", spn=110, fmi=0)
        state = sim.clear_faults("man-tgx")
        assert state["active_dtcs"] == []

    def test_invalid_spn_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.trigger_fault("man-tgx", spn=-1, fmi=0)

    def test_reset_trip_keeps_odometer(self, sim):
        sim.set_speed("man-tgx", 60, instant=True)
        run_ticks(sim, 20)
        before_total = sim.state_of("man-tgx")["odometer_km"]
        state = sim.reset_trip("man-tgx")
        assert state["trip_km"] == 0.0
        assert state["odometer_km"] == pytest.approx(before_total)

    def test_add_vehicle_appears_in_states_and_fleet(self, sim):
        sa = sim.fleet.next_source_address()
        vehicle = build_vehicle(
            brand_id="test",
            brand="Test Brand",
            model_id="x1",
            model="X1",
            segment="Ozel",
            country="-",
            color="#ffffff",
            source_address=sa,
            max_speed_kmh=100,
            power_hp=300,
        )
        sim.fleet.add_vehicle(vehicle)
        state = sim.add_vehicle(vehicle)
        assert state["vehicle_id"] == "test-x1"
        assert sim.fleet.get("test-x1") is not None
        frames = run_ticks(sim, 1)
        assert any(f["vehicle_id"] == "test-x1" for f in frames)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestTelematicsEndpoints:
    def test_dtc_catalog(self, client):
        body = client.get("/api/dtc-catalog").json()
        assert "110" in body["spn_names"]
        assert "0" in body["fmi_names"]

    def test_trigger_and_clear_fault(self, client):
        response = client.post("/api/vehicles/scania-r500/fault", json={"spn": 110, "fmi": 0})
        assert response.status_code == 200
        assert response.json()["state"]["active_dtcs"][0]["spn"] == 110

        cleared = client.delete("/api/vehicles/scania-r500/fault")
        assert cleared.json()["state"]["active_dtcs"] == []

    def test_trip_reset(self, client):
        response = client.post("/api/vehicles/scania-r500/trip/reset")
        assert response.status_code == 200
        assert response.json()["state"]["trip_km"] == 0.0

    def test_add_vehicle_appears_in_fleet(self, client):
        response = client.post(
            "/api/fleet/vehicles",
            json={
                "brand_id": "test-brand",
                "brand": "Test Brand",
                "model_id": "z1",
                "model": "Z1",
                "segment": "Ozel",
                "max_speed_kmh": 100,
                "power_hp": 300,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["vehicle"]["id"] == "test-brand-z1"

        listing = client.get("/api/vehicles/test-brand-z1")
        assert listing.status_code == 200

    def test_ws_fault_commands(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "trigger_fault", "vehicle_id": "man-tgs", "spn": 110, "fmi": 0})
            for _ in range(20):
                message = ws.receive_json()
                if message.get("type") == "ack" and message.get("command") == "trigger_fault":
                    assert message["state"]["active_dtcs"][0]["spn"] == 110
                    break
            else:
                raise AssertionError("trigger_fault ack alinamadi")
