"""Simulator davranis testleri."""

import pytest

from app.config import settings
from app.fleet import load_fleet
from app.j1939 import BIT2_ON, parse_ccvs1_data
from app.simulator import Simulator, SimulatorError


@pytest.fixture
def sim() -> Simulator:
    return Simulator(load_fleet(settings.fleet_file), settings)


def run_ticks(sim: Simulator, count: int) -> list[dict]:
    frames: list[dict] = []
    for _ in range(count):
        frames = sim.tick()
    return frames


class TestFleetWiring:
    def test_thirty_vehicles_ten_brands(self, sim):
        assert len(sim.states) == 30
        assert len(sim.fleet.grouped_by_brand()) == 10

    def test_every_vehicle_emits_one_frame_per_tick(self, sim):
        assert len(sim.tick()) == 30

    def test_source_addresses_are_unique(self, sim):
        addresses = [v.source_address for v in sim.fleet]
        assert len(set(addresses)) == len(addresses)


class TestSpeedInjection:
    def test_speed_ramps_toward_target(self, sim):
        sim.set_speed("man-tgx", 80)
        assert sim.state_of("man-tgx")["speed_kmh"] == 0
        run_ticks(sim, 5)
        speed = sim.state_of("man-tgx")["speed_kmh"]
        assert 0 < speed < 80

    def test_instant_injection_skips_ramp(self, sim):
        sim.set_speed("volvo-fh16", 65, instant=True)
        assert sim.state_of("volvo-fh16")["speed_kmh"] == pytest.approx(65, abs=0.01)

    def test_speed_clamped_to_configured_maximum(self, sim):
        state = sim.set_speed("scania-s730", 5000)
        assert state["target_speed_kmh"] == settings.max_speed_kmh

    def test_injected_speed_appears_in_can_frame(self, sim):
        sim.set_speed("daf-xf", 100, instant=True)
        frame = next(f for f in sim.tick() if f["vehicle_id"] == "daf-xf")
        decoded = parse_ccvs1_data(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_84_wheel_based_speed_kmh"] == pytest.approx(100, abs=1.0)
        assert frame["can_id_hex"] == "18FEF10C"

    def test_unknown_vehicle_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.set_speed("tesla-cybertruck", 50)


class TestBrakeAndCruise:
    def test_brake_brings_vehicle_to_stop(self, sim):
        sim.set_speed("iveco-s-way", 90, instant=True)
        sim.set_brake("iveco-s-way", True)
        run_ticks(sim, 200)
        assert sim.state_of("iveco-s-way")["speed_kmh"] == 0

    def test_brake_switch_encoded_in_byte_four(self, sim):
        sim.set_brake("bmc-tugra", True)
        frame = next(f for f in sim.tick() if f["vehicle_id"] == "bmc-tugra")
        assert parse_ccvs1_data(bytes.fromhex(frame["data_hex"]))["spn_597_brake_switch"] == BIT2_ON

    def test_cruise_holds_set_speed(self, sim):
        sim.set_speed("ford-trucks-f-max", 85, instant=True)
        sim.set_cruise("ford-trucks-f-max", True)
        state = sim.state_of("ford-trucks-f-max")
        assert state["cruise_active"] is True
        assert state["cruise_set_speed_kmh"] == 85

    def test_cruise_set_speed_reaches_spn_86(self, sim):
        sim.set_cruise("renault-trucks-t-high", True, set_speed_kmh=88)
        frame = next(f for f in sim.tick() if f["vehicle_id"] == "renault-trucks-t-high")
        assert (
            parse_ccvs1_data(bytes.fromhex(frame["data_hex"]))["spn_86_cruise_set_speed_kmh"] == 88
        )


class TestOfflineBehaviour:
    def test_offline_vehicle_is_silent_on_bus(self, sim):
        sim.set_online("isuzu-npr", False)
        frames = sim.tick()
        assert len(frames) == 29
        assert all(f["vehicle_id"] != "isuzu-npr" for f in frames)

    def test_offline_vehicle_speed_is_zero(self, sim):
        sim.set_speed("isuzu-npr", 70, instant=True)
        sim.set_online("isuzu-npr", False)
        sim.tick()
        assert sim.state_of("isuzu-npr")["speed_kmh"] == 0

    def test_vehicle_can_be_brought_back_online(self, sim):
        sim.set_online("isuzu-npr", False)
        sim.set_online("isuzu-npr", True)
        assert len(sim.tick()) == 30


class TestModesAndFleetCommands:
    def test_auto_mode_generates_movement(self, sim):
        sim.set_mode("mercedes-benz-actros", "auto")
        run_ticks(sim, 50)
        assert sim.state_of("mercedes-benz-actros")["speed_kmh"] > 0

    def test_invalid_mode_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.set_mode("mercedes-benz-actros", "warp")

    def test_stop_all_zeroes_the_fleet(self, sim):
        sim.fleet_command("auto_all")
        run_ticks(sim, 30)
        sim.fleet_command("stop_all")
        run_ticks(sim, 400)
        assert all(s.speed_kmh == 0 for s in sim.states.values())

    def test_unknown_fleet_command_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.fleet_command("launch_all")


class TestSnapshot:
    def test_snapshot_contains_fleet_states_and_meta(self, sim):
        snapshot = sim.snapshot()
        assert len(snapshot["brands"]) == 10
        assert len(snapshot["states"]) == 30
        assert snapshot["meta"]["pgn"] == 65265
        assert snapshot["meta"]["vehicle_count"] == 30

    def test_stats_track_transmitted_frames(self, sim):
        run_ticks(sim, 10)
        assert sim.stats()["frames_sent"] == 300
