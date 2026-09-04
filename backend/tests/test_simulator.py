"""Simulator davranis testleri."""

import pytest

from app.config import settings
from app.fleet import load_fleet
from app.j1939 import (
    MESSAGES,
    PGN_CCVS1,
    PGN_EBC1,
    PGN_EEC2,
    PGN_ETC2,
    PGN_HVBATT,
    parse_ccvs1,
    parse_ebc1,
    parse_eec2,
    parse_etc2,
    parse_hvbatt,
)
from app.simulator import Simulator, SimulatorError


@pytest.fixture
def sim() -> Simulator:
    return Simulator(load_fleet(settings.fleet_file), settings)


def run_ticks(sim: Simulator, count: int) -> list[dict]:
    frames: list[dict] = []
    for _ in range(count):
        frames = sim.tick()
    return frames


def frame_of(frames: list[dict], vehicle_id: str, pgn: int) -> dict:
    return next(f for f in frames if f["vehicle_id"] == vehicle_id and f["pgn"] == pgn)


class TestFleetWiring:
    def test_thirty_vehicles_ten_brands(self, sim):
        assert len(sim.states) == 30
        assert len(sim.fleet.grouped_by_brand()) == 10

    def test_source_addresses_are_unique(self, sim):
        addresses = [v.source_address for v in sim.fleet]
        assert len(set(addresses)) == len(addresses)

    def test_each_vehicle_has_an_id_per_message(self, sim):
        vehicle = sim.fleet.get("volvo-fm")
        assert set(vehicle.can_ids) == set(MESSAGES)


class TestTransmitRates:
    def test_fast_messages_emit_every_tick(self, sim):
        frames = sim.tick()
        # 30 arac x 4 mesaj (HVBATT haric)
        assert len(frames) == 120
        assert {f["pgn"] for f in frames} == {PGN_CCVS1, PGN_EEC2, PGN_ETC2, PGN_EBC1}

    def test_battery_emits_once_per_second(self, sim):
        battery_ticks = sum(1 for _ in range(10) if any(f["pgn"] == PGN_HVBATT for f in sim.tick()))
        assert battery_ticks == 1

    def test_offline_vehicle_is_silent_on_all_pgns(self, sim):
        sim.set_online("isuzu-elf", False)
        frames = sim.tick()
        assert all(f["vehicle_id"] != "isuzu-elf" for f in frames)
        assert len(frames) == 29 * 4


class TestSpeedInjection:
    def test_speed_ramps_toward_target(self, sim):
        sim.set_speed("man-tgx", 80)
        run_ticks(sim, 5)
        assert 0 < sim.state_of("man-tgx")["speed_kmh"] < 80

    def test_instant_injection_skips_ramp(self, sim):
        sim.set_speed("volvo-fh16", 65, instant=True)
        assert sim.state_of("volvo-fh16")["speed_kmh"] == pytest.approx(65, abs=0.01)

    def test_injected_speed_appears_in_ccvs1(self, sim):
        sim.set_speed("daf-xf", 100, instant=True)
        frame = frame_of(sim.tick(), "daf-xf", PGN_CCVS1)
        decoded = parse_ccvs1(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_84_wheel_based_speed_kmh"] == pytest.approx(100, abs=1.0)
        assert frame["can_id_hex"] == "18FEF10C"

    def test_unknown_vehicle_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.set_speed("tesla-cybertruck", 50)


class TestAcceleratorPedal:
    def test_pedal_sets_proportional_target(self, sim):
        vehicle = sim.fleet.get("scania-r500")
        state = sim.set_accelerator("scania-r500", 50)
        assert state["accel_pedal_pct"] == 50
        # hedef, aracin tavan hizinin yuzdesi kadar olmali
        assert state["target_speed_kmh"] == pytest.approx(vehicle.max_speed_kmh * 0.5, abs=0.1)

    def test_pedal_reaches_eec2_spn_91(self, sim):
        sim.set_accelerator("scania-r500", 60)
        frame = frame_of(sim.tick(), "scania-r500", PGN_EEC2)
        decoded = parse_eec2(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_91_accelerator_pedal_position_1_pct"] == pytest.approx(60, abs=0.4)

    def test_pedal_moves_the_vehicle(self, sim):
        sim.set_accelerator("scania-r500", 80)
        run_ticks(sim, 40)
        assert sim.state_of("scania-r500")["speed_kmh"] > 10

    def test_pedal_derived_when_speed_injected(self, sim):
        sim.set_speed("man-tgs", 70)
        run_ticks(sim, 10)
        assert sim.state_of("man-tgs")["accel_pedal_pct"] > 0

    def test_pedal_released_under_braking(self, sim):
        sim.set_accelerator("man-tgs", 70)
        run_ticks(sim, 10)
        sim.set_brake_pedal("man-tgs", 80)
        run_ticks(sim, 5)
        assert sim.state_of("man-tgs")["accel_pedal_pct"] == 0


class TestBrakePedal:
    def test_brake_pedal_reaches_ebc1_spn_521(self, sim):
        sim.set_brake_pedal("iveco-s-way", 75)
        frame = frame_of(sim.tick(), "iveco-s-way", PGN_EBC1)
        decoded = parse_ebc1(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_521_brake_pedal_position_pct"] == pytest.approx(75, abs=0.4)

    def test_brake_pedal_opens_ccvs1_brake_switch(self, sim):
        sim.set_brake_pedal("iveco-s-way", 40)
        frame = frame_of(sim.tick(), "iveco-s-way", PGN_CCVS1)
        assert parse_ccvs1(bytes.fromhex(frame["data_hex"]))["spn_597_brake_switch"] == 0b01

    def test_released_pedal_closes_switch(self, sim):
        sim.set_brake_pedal("iveco-s-way", 0)
        frame = frame_of(sim.tick(), "iveco-s-way", PGN_CCVS1)
        assert parse_ccvs1(bytes.fromhex(frame["data_hex"]))["spn_597_brake_switch"] == 0b00

    def test_brake_brings_vehicle_to_stop(self, sim):
        sim.set_speed("iveco-s-way", 90, instant=True)
        sim.set_brake_pedal("iveco-s-way", 100)
        run_ticks(sim, 200)
        assert sim.state_of("iveco-s-way")["speed_kmh"] == 0

    def test_harder_pedal_stops_sooner(self, sim):
        sim.set_speed("daf-xd", 80, instant=True)
        sim.set_brake_pedal("daf-xd", 20)
        run_ticks(sim, 10)
        yumusak = sim.state_of("daf-xd")["speed_kmh"]

        sim.set_speed("daf-xd", 80, instant=True)
        sim.set_brake_pedal("daf-xd", 100)
        run_ticks(sim, 10)
        assert sim.state_of("daf-xd")["speed_kmh"] < yumusak


class TestTransmission:
    def test_gear_climbs_with_speed(self, sim):
        sim.set_speed("scania-s730", 20, instant=True)
        sim.tick()
        dusuk = sim.state_of("scania-s730")["gear"]
        sim.set_speed("scania-s730", 90, instant=True)
        sim.tick()
        assert sim.state_of("scania-s730")["gear"] > dusuk

    def test_gear_reaches_etc2_spn_523(self, sim):
        sim.set_speed("scania-s730", 90, instant=True)
        frame = frame_of(sim.tick(), "scania-s730", PGN_ETC2)
        decoded = parse_etc2(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_523_current_gear"] == sim.state_of("scania-s730")["gear"]

    def test_gear_never_exceeds_model_gear_count(self, sim):
        vehicle = sim.fleet.get("isuzu-elf")
        sim.set_speed("isuzu-elf", 180, instant=True)
        run_ticks(sim, 20)
        assert sim.state_of("isuzu-elf")["gear"] <= vehicle.gear_count

    @pytest.mark.parametrize("gear_range,expected", [("P", 0), ("N", 0), ("R", -1)])
    def test_range_sets_gear(self, sim, gear_range, expected):
        state = sim.set_gear_range("volvo-fmx", gear_range)
        assert state["gear_range"] == gear_range
        assert state["gear"] == expected

    def test_range_reaches_etc2_ascii_field(self, sim):
        sim.set_gear_range("volvo-fmx", "R")
        frame = frame_of(sim.tick(), "volvo-fmx", PGN_ETC2)
        assert parse_etc2(bytes.fromhex(frame["data_hex"]))["spn_163_current_range"] == "R"

    def test_park_holds_vehicle_still(self, sim):
        sim.set_speed("volvo-fmx", 60, instant=True)
        sim.set_gear_range("volvo-fmx", "P")
        run_ticks(sim, 200)
        assert sim.state_of("volvo-fmx")["speed_kmh"] == 0

    def test_reverse_is_speed_limited(self, sim):
        sim.set_gear_range("volvo-fmx", "R")
        sim.set_speed("volvo-fmx", 120)
        sim.set_gear_range("volvo-fmx", "R")
        run_ticks(sim, 200)
        assert sim.state_of("volvo-fmx")["speed_kmh"] <= 21

    def test_invalid_range_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.set_gear_range("volvo-fmx", "X")

    def test_manual_gear_disables_auto_selection(self, sim):
        state = sim.set_gear("man-tgx", 5)
        assert state["gear"] == 5 and state["gear_auto"] is False
        sim.set_speed("man-tgx", 90, instant=True)
        run_ticks(sim, 10)
        assert sim.state_of("man-tgx")["gear"] == 5

    def test_gear_out_of_range_rejected(self, sim):
        with pytest.raises(SimulatorError):
            sim.set_gear("isuzu-elf", 15)  # 6 vitesli model


class TestEngine:
    def test_rpm_drops_on_upshift(self, sim):
        sim.set_speed("scania-s730", 90)
        devirler, vitesler = [], []
        for _ in range(220):
            sim.tick()
            state = sim.state_of("scania-s730")
            devirler.append(state["engine_rpm"])
            vitesler.append(state["gear"])
        # En az bir vites yukseltmesinde devir dusmus olmali
        dususler = [
            i
            for i in range(1, len(vitesler))
            if vitesler[i] > vitesler[i - 1] and devirler[i] < devirler[i - 1]
        ]
        assert dususler, "vites yukseltmesinde devir dusmedi"

    def test_electric_vehicle_has_no_idle(self, sim):
        sim.set_speed("volvo-fm", 0, instant=True)
        sim.tick()
        assert sim.state_of("volvo-fm")["engine_rpm"] == 0

    def test_engine_load_follows_pedal(self, sim):
        sim.set_accelerator("daf-xf", 0)
        sim.tick()
        bos = sim.state_of("daf-xf")["engine_load_pct"]
        sim.set_accelerator("daf-xf", 90)
        sim.tick()
        assert sim.state_of("daf-xf")["engine_load_pct"] > bos


class TestBattery:
    def test_soc_and_soh_reach_hvbatt(self, sim):
        sim.set_battery("volvo-fm", soc_pct=64, soh_pct=88)
        frames = run_ticks(sim, 10)
        frame = frame_of(frames, "volvo-fm", PGN_HVBATT)
        decoded = parse_hvbatt(bytes.fromhex(frame["data_hex"]))
        assert decoded["spn_5464_state_of_charge_pct"] == pytest.approx(64, abs=0.5)
        assert decoded["spn_5465_state_of_health_pct"] == pytest.approx(88, abs=0.5)

    def test_electric_vehicle_drains_while_driving(self, sim):
        sim.set_battery("volvo-fm", soc_pct=80)
        sim.set_speed("volvo-fm", 80, instant=True)
        run_ticks(sim, 100)
        assert sim.state_of("volvo-fm")["soc_pct"] < 80

    def test_diesel_battery_stays_charged(self, sim):
        sim.set_battery("scania-s730", soc_pct=60)
        run_ticks(sim, 200)
        assert sim.state_of("scania-s730")["soc_pct"] > 80

    def test_values_are_clamped(self, sim):
        assert sim.set_battery("volvo-fm", soc_pct=500)["soc_pct"] == 100
        assert sim.set_battery("volvo-fm", soh_pct=-5)["soh_pct"] == 0


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
        assert len(snapshot["meta"]["messages"]) == 9

    def test_light_states_omit_frames(self, sim):
        sim.tick()
        light = sim.light_states()
        assert "last_frames" not in light["man-tgx"]
        assert "gear" in light["man-tgx"] and "soc_pct" in light["man-tgx"]

    def test_stats_track_transmitted_frames(self, sim):
        run_ticks(sim, 10)
        # CCVS1/EEC2/ETC2/EBC1 her tick (4x10=40) + HVBATT/DM1/VEP1/VDHR
        # tick 10'da bir kez (4x1=4) = 44 cerceve/arac; HOURS 50 tick'te bir,
        # 10 tick icinde henuz yayinlanmadi.
        assert sim.stats()["frames_sent"] == 30 * 44
        assert sim.stats()["message_count"] == 9
