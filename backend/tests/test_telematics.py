"""Filo telematik ozellikleri: DM1 ariza tetikleme, konum, mesafe, arac ekleme."""

import base64

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

    def test_add_vehicle_with_image_is_served_back(self, client):
        # 1x1 kirmizi piksel PNG - gercek bir gorsel yukleme senaryosunu tam
        # olarak tetikler (backend/frontend ayri container oldugu icin
        # yuklenen dosya /uploads uzerinden servis edilmeli).
        png_1x1 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
            "42YAAAAASUVORK5CYII="
        )
        response = client.post(
            "/api/fleet/vehicles",
            json={
                "brand_id": "test-brand",
                "brand": "Test Brand",
                "model_id": "img1",
                "model": "IMG1",
                "segment": "Ozel",
                "max_speed_kmh": 100,
                "power_hp": 300,
                "image_data_url": f"data:image/png;base64,{png_1x1}",
            },
        )
        assert response.status_code == 201
        vehicle = response.json()["vehicle"]
        assert vehicle["image"] == "uploads/test-brand-img1.jpg"

        served = client.get(f"/{vehicle['image']}")
        assert served.status_code == 200
        assert served.content == base64.b64decode(png_1x1)

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


class TestDbcDerivedSignals:
    """DBC sinyallerinin surus durumundan tutarli sekilde turetildigi."""

    def test_engine_warms_up_from_ambient(self, sim):
        state = sim.states["man-tgx"]
        soguk = state.coolant_temp_c
        assert soguk == pytest.approx(state.ambient_air_temp_c, abs=0.1)
        sim.set_speed("man-tgx", 80, instant=True)
        run_ticks(sim, 900)  # 90 saniye
        isinmis = sim.state_of("man-tgx")["engine"]
        assert isinmis["coolant_temp_c"] > soguk + 20
        # Yag sogutma suyunu geriden takip eder
        assert isinmis["oil_temp_c"] > soguk

    def test_fuel_consumption_drains_tank_and_tracks_economy(self, sim):
        onceki = sim.state_of("man-tgx")["ambient"]["fuel_level_pct"]
        sim.set_speed("man-tgx", 90, instant=True)
        run_ticks(sim, 600)
        state = sim.state_of("man-tgx")
        assert state["engine"]["fuel_rate_lph"] > 0
        assert state["engine"]["instant_fuel_economy_kmpl"] > 0
        assert state["ambient"]["fuel_level_pct"] < onceki

    def test_electric_vehicle_reports_no_fuel_rate(self, sim):
        elektrikli = next(v for v in sim.fleet if v.powertrain == "electric")
        sim.set_speed(elektrikli.id, 70, instant=True)
        run_ticks(sim, 50)
        assert sim.state_of(elektrikli.id)["engine"]["fuel_rate_lph"] == 0.0

    def test_shaft_speeds_follow_gear_ratio(self, sim):
        sim.set_speed("man-tgx", 85, instant=True)
        run_ticks(sim, 60)
        state = sim.state_of("man-tgx")
        aktarma = state["transmission"]
        # Cikis mili = motor devri / aktarma orani
        assert aktarma["output_shaft_rpm"] == pytest.approx(
            state["engine_rpm"] / aktarma["gear_ratio"], rel=0.02
        )
        assert aktarma["driveline_engaged"] is True

    def test_hard_braking_activates_abs_and_spreads_wheel_speeds(self, sim):
        sim.set_speed("man-tgx", 90, instant=True)
        run_ticks(sim, 5)
        sakin = sim.state_of("man-tgx")["brakes"]["wheel_slip"]
        sim.set_brake_pedal("man-tgx", 95)
        run_ticks(sim, 3)
        frenli = sim.state_of("man-tgx")["brakes"]
        assert frenli["abs_active"] is True
        assert frenli["total_brake_demand_pct"] == pytest.approx(95.0)
        assert max(abs(x) for x in frenli["wheel_slip"]) > max(abs(x) for x in sakin)

    def test_gear_change_opens_shift_and_clutch_window(self, sim):
        sim.set_speed("man-tgx", 20, instant=True)
        sim.tick()
        sim.set_speed("man-tgx", 90, instant=True)
        sim.tick()
        assert sim.states["man-tgx"].shift_in_process is True
        assert sim.states["man-tgx"].clutch_switch is True

    def test_fleet_starts_with_varied_environments(self, sim):
        sicakliklar = {round(s.ambient_air_temp_c, 1) for s in sim.states.values()}
        depolar = {round(s.fuel_level_pct, 1) for s in sim.states.values()}
        # 30 arac ayni degerle baslamamali
        assert len(sicakliklar) > 15
        assert len(depolar) > 15


class TestDbcCommandEndpoints:
    def test_trailer_toggle(self, client):
        body = client.post("/api/vehicles/man-tgx/trailer", json={"value": True}).json()
        assert body["state"]["brakes"]["trailer_connected"] is True
        body = client.post("/api/vehicles/man-tgx/trailer", json={"value": False}).json()
        assert body["state"]["brakes"]["trailer_connected"] is False

    def test_fuel_levels(self, client):
        body = client.post(
            "/api/vehicles/man-tgx/fuel", json={"fuel_level_pct": 42, "fuel_level2_pct": 17}
        ).json()
        assert body["state"]["ambient"]["fuel_level_pct"] == 42
        assert body["state"]["ambient"]["fuel_level2_pct"] == 17

    def test_fuel_requires_a_field(self, client):
        assert client.post("/api/vehicles/man-tgx/fuel", json={}).status_code == 422

    def test_ambient_temperature(self, client):
        body = client.post(
            "/api/vehicles/man-tgx/ambient", json={"ambient_air_temp_c": -12.5}
        ).json()
        assert body["state"]["ambient"]["ambient_air_temp_c"] == -12.5

    def test_ambient_out_of_range_rejected(self, client):
        assert (
            client.post("/api/vehicles/man-tgx/ambient", json={"ambient_air_temp_c": 200})
        ).status_code == 422

    def test_pto_state(self, client):
        body = client.post("/api/vehicles/man-tgx/pto", json={"pto_state": 4}).json()
        assert body["state"]["ambient"]["pto_state"] == 4

    def test_pto_out_of_range_rejected(self, client):
        assert client.post("/api/vehicles/man-tgx/pto", json={"pto_state": 20}).status_code == 422
