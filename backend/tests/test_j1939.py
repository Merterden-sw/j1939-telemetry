"""J1939 kodlama/cozme birim testleri."""

import pytest

from app.j1939 import (
    MESSAGES,
    PGN_CCVS1,
    PGN_DM1,
    PGN_EBC1,
    PGN_EEC2,
    PGN_ETC2,
    PGN_HVBATT,
    SPN84_MAX_KMH,
    J1939Error,
    build_amb,
    build_can_id,
    build_ccss,
    build_ccvs1,
    build_ccvs1_frame,
    build_dd,
    build_dm1,
    build_ebc1,
    build_ebc2,
    build_eec1,
    build_eec2,
    build_eflp1,
    build_et1,
    build_etc1,
    build_etc2,
    build_frame,
    build_hours,
    build_hvbatt,
    build_ic1,
    build_lfe1,
    build_vdhr,
    build_vep1,
    decode_can_id,
    decode_gear,
    decode_percent,
    decode_range,
    decode_wheel_speed,
    encode_gear,
    encode_percent,
    encode_range,
    encode_wheel_speed,
    pack_2bit,
    parse_amb,
    parse_ccss,
    parse_ccvs1,
    parse_dd,
    parse_dm1,
    parse_ebc1,
    parse_ebc2,
    parse_eec1,
    parse_eec2,
    parse_eflp1,
    parse_et1,
    parse_etc1,
    parse_etc2,
    parse_frame,
    parse_hours,
    parse_hvbatt,
    parse_ic1,
    parse_lfe1,
    parse_vdhr,
    parse_vep1,
    unpack_2bit,
    wheel_speed_bytes,
)


class TestCanIdentifier:
    @pytest.mark.parametrize(
        "pgn,priority,sa,expected",
        [
            (PGN_CCVS1, 6, 0x00, 0x18FEF100),
            (PGN_CCVS1, 6, 0x1D, 0x18FEF11D),
            (PGN_EEC2, 3, 0x00, 0x0CF00300),
            (PGN_ETC2, 3, 0x0A, 0x0CF0050A),
            (PGN_EBC1, 6, 0x00, 0x18F00100),
            (PGN_HVBATT, 6, 0x07, 0x18FD9B07),
        ],
    )
    def test_identifiers_match_spec(self, pgn, priority, sa, expected):
        assert build_can_id(pgn, source_address=sa, priority=priority) == expected

    def test_all_registry_pgns_are_pdu2(self):
        # Tum yayin mesajlarinda PF >= 240 olmali (hedef adres tasimaz)
        for pgn in MESSAGES:
            assert decode_can_id(build_can_id(pgn))["pdu_type"] == "PDU2"

    def test_decode_roundtrip(self):
        decoded = decode_can_id(build_can_id(PGN_ETC2, source_address=0x0A, priority=3))
        assert decoded["pgn"] == PGN_ETC2
        assert decoded["pgn_hex"] == "0xF005"
        assert decoded["priority"] == 3
        assert decoded["source_address"] == 0x0A

    def test_pdu1_uses_ps_as_destination(self):
        can_id = build_can_id(59904, source_address=0x01, destination_address=0x21)
        assert decode_can_id(can_id)["destination_address"] == 0x21

    @pytest.mark.parametrize("priority", [-1, 8])
    def test_invalid_priority_rejected(self, priority):
        with pytest.raises(J1939Error):
            build_can_id(PGN_CCVS1, source_address=0, priority=priority)


class TestSpn84WheelSpeed:
    @pytest.mark.parametrize(
        "kmh,raw",
        [
            (0.0, 0x0000),
            (1.0, 0x0100),
            (60.0, 0x3C00),
            (90.0, 0x5A00),
            (180.0, 0xB400),
            (250.996, 0xFAFF),
        ],
    )
    def test_resolution_is_one_over_256(self, kmh, raw):
        assert encode_wheel_speed(kmh) == raw

    def test_little_endian_byte_order(self):
        assert wheel_speed_bytes(60.0) == (0x00, 0x3C)
        assert wheel_speed_bytes(87.5) == (0x80, 0x57)

    def test_clamped_and_not_available(self):
        assert encode_wheel_speed(999) == 0xFAFF
        assert encode_wheel_speed(None) == 0xFFFF
        assert decode_wheel_speed(0xFFFF) is None

    @pytest.mark.parametrize("kmh", [0, 12.5, 37.75, 88.0, 150.25, 180.0])
    def test_roundtrip(self, kmh):
        assert decode_wheel_speed(encode_wheel_speed(kmh)) == pytest.approx(kmh, abs=0.004)

    def test_max_value(self):
        assert SPN84_MAX_KMH == pytest.approx(250.996, abs=0.001)


class TestPercentSignals:
    """SPN 91 / 521 / 5464 / 5465 - 0.4 %/bit"""

    @pytest.mark.parametrize("pct,raw", [(0, 0), (20, 50), (40, 100), (100, 250)])
    def test_resolution_is_zero_point_four(self, pct, raw):
        assert encode_percent(pct) == raw

    def test_clamped_to_hundred_percent(self):
        assert encode_percent(150) == 250
        assert decode_percent(250) == 100.0

    def test_not_available(self):
        assert encode_percent(None) == 0xFF
        assert decode_percent(0xFF) is None

    def test_quantisation_is_within_resolution(self):
        # %45 tam olarak temsil edilemez (0.4'un tam kati degil)
        assert decode_percent(encode_percent(45)) == pytest.approx(45, abs=0.4)


class TestGearEncoding:
    """SPN 523 / 524 - offset -125"""

    @pytest.mark.parametrize("gear,raw", [(0, 125), (1, 126), (8, 133), (-1, 124), (-2, 123)])
    def test_offset_minus_125(self, gear, raw):
        assert encode_gear(gear) == raw
        assert decode_gear(raw) == gear

    def test_not_available(self):
        assert encode_gear(None) == 0xFF
        assert decode_gear(0xFF) is None

    def test_out_of_range_rejected(self):
        with pytest.raises(J1939Error):
            encode_gear(200)

    @pytest.mark.parametrize("label", ["P", "R", "N", "D"])
    def test_range_ascii_roundtrip(self, label):
        assert decode_range(encode_range(label)) == label

    def test_range_is_two_ascii_bytes(self):
        assert encode_range("D") == b"D "
        assert encode_range(None) == b"\xff\xff"
        assert decode_range(b"\xff\xff") is None


class TestBitPacking:
    def test_roundtrip(self):
        assert unpack_2bit(pack_2bit(0b01, 0b00, 0b11, 0b10)) == (0b01, 0b00, 0b11, 0b10)

    def test_bit_positions(self):
        assert pack_2bit(0b11, 0, 0, 0) == 0x03
        assert pack_2bit(0, 0, 0, 0b11) == 0xC0

    def test_out_of_range_rejected(self):
        with pytest.raises(J1939Error):
            pack_2bit(4, 0, 0, 0)


class TestCcvs1:
    def test_speed_lands_in_bytes_two_and_three(self):
        data = build_ccvs1({"speed_kmh": 60.0})
        assert data[1] == 0x00 and data[2] == 0x3C

    def test_documented_frame_is_stable(self):
        # README ve CI duman testi bu degere dayaniyor
        assert build_ccvs1_frame(60.0, 0).candump == "18FEF100#F3003C0000FF1FFF"

    def test_switches_are_reflected(self):
        signals = parse_ccvs1(
            build_ccvs1({"speed_kmh": 0.0, "parking_brake": True, "brake_switch": True})
        )
        assert signals["spn_70_parking_brake"] == 0b01
        assert signals["spn_597_brake_switch"] == 0b01

    def test_cruise_set_speed_byte_six(self):
        data = build_ccvs1({"speed_kmh": 90.0, "cruise_set_speed_kmh": 88})
        assert data[5] == 88
        assert parse_ccvs1(data)["spn_86_cruise_set_speed_kmh"] == 88


class TestEec2:
    """PGN 61443 - gaz pedali"""

    def test_accelerator_pedal_in_byte_two(self):
        data = build_eec2({"accel_pedal_pct": 50})
        assert data[1] == 125  # 50 / 0.4
        assert parse_eec2(data)["spn_91_accelerator_pedal_position_1_pct"] == 50.0

    def test_engine_load_in_byte_three(self):
        data = build_eec2({"accel_pedal_pct": 0, "engine_load_pct": 42})
        assert data[2] == 42
        assert parse_eec2(data)["spn_92_engine_percent_load_pct"] == 42

    def test_low_idle_switch_set_when_pedal_released(self):
        assert parse_eec2(build_eec2({"accel_pedal_pct": 0.0}))["spn_558_low_idle_switch"] == 0b01
        assert parse_eec2(build_eec2({"accel_pedal_pct": 40.0}))["spn_558_low_idle_switch"] == 0b00

    def test_kickdown_switch(self):
        assert (
            parse_eec2(build_eec2({"accel_pedal_pct": 100, "kickdown": True}))[
                "spn_559_kickdown_switch"
            ]
            == 0b01
        )

    def test_unused_bytes_are_not_available(self):
        assert build_eec2({"accel_pedal_pct": 10})[5:] == b"\xff\xff\xff"


class TestEtc2:
    """PGN 61445 - vites"""

    def test_current_gear_in_byte_four(self):
        data = build_etc2({"current_gear": 8, "selected_gear": 8})
        assert data[3] == 133
        assert parse_etc2(data)["spn_523_current_gear"] == 8

    def test_gear_ratio_little_endian(self):
        data = build_etc2({"current_gear": 4, "selected_gear": 4, "gear_ratio": 1.5})
        assert data[1] == 0xDC and data[2] == 0x05  # 1500
        assert parse_etc2(data)["spn_526_actual_gear_ratio"] == 1.5

    def test_range_fields_are_ascii(self):
        data = build_etc2(
            {"current_gear": 1, "selected_gear": 1, "current_range": "D", "requested_range": "D"}
        )
        assert data[4:6] == b"D " and data[6:8] == b"D "
        assert parse_etc2(data)["spn_163_current_range"] == "D"

    def test_reverse_gear(self):
        data = build_etc2({"current_gear": -1, "selected_gear": -1, "current_range": "R"})
        assert parse_etc2(data)["spn_523_current_gear"] == -1
        assert parse_etc2(data)["spn_163_current_range"] == "R"


class TestEbc1:
    """PGN 61441 - fren pedali"""

    def test_brake_pedal_position_in_byte_two(self):
        data = build_ebc1({"brake_pedal_pct": 60})
        assert data[1] == 150  # 60 / 0.4
        assert parse_ebc1(data)["spn_521_brake_pedal_position_pct"] == 60.0

    def test_switch_bits(self):
        signals = parse_ebc1(
            build_ebc1({"brake_pedal_pct": 0, "abs_active": True, "ebs_brake_switch": True})
        )
        assert signals["spn_563_abs_active"] == 0b01
        assert signals["spn_1121_ebs_brake_switch"] == 0b01


class TestHvBattery:
    """PGN 64923 - batarya SOC / SOH"""

    def test_soc_and_soh_bytes(self):
        data = build_hvbatt({"soc_pct": 78, "soh_pct": 96})
        assert data[0] == 195 and data[1] == 240
        signals = parse_hvbatt(data)
        assert signals["spn_5464_state_of_charge_pct"] == 78.0
        assert signals["spn_5465_state_of_health_pct"] == 96.0

    def test_unused_bytes_are_not_available(self):
        assert build_hvbatt({"soc_pct": 50, "soh_pct": 50})[2:] == b"\xff" * 6


class TestDbcMessages:
    """DBC dosyasindan gelen mesajlarin kodla-coz dongusu."""

    def test_eec1_round_trip(self):
        data = build_eec1(
            {
                "torque_mode": 1,
                "driver_demand_torque_pct": 42,
                "actual_engine_torque_pct": -30,
                "engine_rpm": 1450.0,
                "engine_source_address": 0x00,
                "starter_mode": 2,
                "demand_engine_torque_pct": 40,
            }
        )
        out = parse_eec1(data)
        assert out["spn_899_engine_torque_mode"] == 1
        assert out["spn_512_driver_demand_torque_pct"] == 42
        assert out["spn_513_actual_engine_torque_pct"] == -30
        assert out["spn_190_engine_speed_rpm"] == pytest.approx(1450.0)
        assert out["spn_1483_engine_source_address"] == 0
        assert out["spn_1675_starter_mode"] == 2
        assert out["spn_2432_demand_engine_torque_pct"] == 40

    def test_eec1_torque_offset_covers_negative_range(self):
        """SPN 512/513: -125..+125 %, offset -125 ile tek byte'a sigar."""
        for value in (-125, -60, 0, 60, 125):
            data = build_eec1({"driver_demand_torque_pct": value})
            assert parse_eec1(data)["spn_512_driver_demand_torque_pct"] == value

    def test_etc1_round_trip(self):
        data = build_etc1(
            {
                "driveline_engaged": True,
                "torque_converter_lockup": True,
                "shift_in_process": False,
                "output_shaft_rpm": 1326.0,
                "input_shaft_rpm": 1034.0,
                "clutch_slip_pct": 12.4,
                "transmission_source_address": 3,
            }
        )
        out = parse_etc1(data)
        assert out["spn_560_driveline_engaged"] == 1
        assert out["spn_573_torque_converter_lockup"] == 1
        assert out["spn_574_shift_in_process"] == 0
        assert out["spn_191_output_shaft_rpm"] == pytest.approx(1326.0)
        assert out["spn_161_input_shaft_rpm"] == pytest.approx(1034.0)
        assert out["spn_522_clutch_slip_pct"] == pytest.approx(12.4)
        assert out["spn_1482_transmission_source_address"] == 3

    def test_ebc2_relative_speeds_signed_range(self):
        """SPN 905-910: offset -7.8125, 1/16 km/h cozunurluk."""
        data = build_ebc2(
            {
                "front_axle_speed_kmh": 87.5,
                "rel_speed_front_left": -7.8125,
                "rel_speed_front_right": 0.0,
                "rel_speed_rear1_left": 7.8125,
                "rel_speed_rear1_right": -0.25,
            }
        )
        out = parse_ebc2(data)
        assert out["spn_904_front_axle_speed_kmh"] == pytest.approx(87.5)
        assert out["spn_905_rel_speed_front_left_kmh"] == pytest.approx(-7.8125)
        assert out["spn_906_rel_speed_front_right_kmh"] == pytest.approx(0.0)
        assert out["spn_907_rel_speed_rear1_left_kmh"] == pytest.approx(7.8125)
        assert out["spn_908_rel_speed_rear1_right_kmh"] == pytest.approx(-0.25)

    def test_lfe1_round_trip(self):
        data = build_lfe1(
            {
                "fuel_rate_lph": 28.4,
                "instant_fuel_economy_kmpl": 3.08,
                "average_fuel_economy_kmpl": 3.4,
                "throttle_valve_pct": 36.0,
            }
        )
        out = parse_lfe1(data)
        assert out["spn_183_fuel_rate_lph"] == pytest.approx(28.4, abs=0.05)
        assert out["spn_184_instant_fuel_economy_kmpl"] == pytest.approx(3.08, abs=0.01)
        assert out["spn_185_average_fuel_economy_kmpl"] == pytest.approx(3.4, abs=0.01)
        assert out["spn_51_throttle_valve_pct"] == pytest.approx(36.0)

    def test_et1_round_trip(self):
        data = build_et1(
            {
                "coolant_temp_c": 88,
                "fuel_temp_c": -40,
                "oil_temp_c": 104.5,
                "turbo_oil_temp_c": 118.0,
            }
        )
        out = parse_et1(data)
        assert out["spn_110_coolant_temp_c"] == 88
        assert out["spn_174_fuel_temp_c"] == -40
        assert out["spn_175_oil_temp_c"] == pytest.approx(104.5)
        assert out["spn_176_turbo_oil_temp_c"] == pytest.approx(118.0)

    def test_eflp1_round_trip(self):
        data = build_eflp1(
            {
                "fuel_delivery_pressure_kpa": 380,
                "oil_level_pct": 86,
                "oil_pressure_kpa": 412,
                "coolant_pressure_kpa": 120,
                "coolant_level_pct": 92,
            }
        )
        out = parse_eflp1(data)
        assert out["spn_94_fuel_delivery_pressure_kpa"] == 380
        assert out["spn_98_oil_level_pct"] == pytest.approx(86.0)
        assert out["spn_100_oil_pressure_kpa"] == 412
        assert out["spn_109_coolant_pressure_kpa"] == 120
        assert out["spn_111_coolant_level_pct"] == pytest.approx(92.0)

    def test_ic1_round_trip(self):
        data = build_ic1(
            {
                "particulate_trap_pressure_kpa": 3.5,
                "boost_pressure_kpa": 168,
                "intake_manifold_temp_c": 52,
                "air_filter_diff_pressure_kpa": 1.15,
                "exhaust_gas_temp_c": 421.5,
                "coolant_filter_diff_pressure_kpa": 2.0,
            }
        )
        out = parse_ic1(data)
        assert out["spn_81_particulate_trap_pressure_kpa"] == pytest.approx(3.5)
        assert out["spn_102_boost_pressure_kpa"] == 168
        assert out["spn_105_intake_manifold_temp_c"] == 52
        assert out["spn_107_air_filter_diff_pressure_kpa"] == pytest.approx(1.15)
        assert out["spn_173_exhaust_gas_temp_c"] == pytest.approx(421.5)
        assert out["spn_112_coolant_filter_diff_pressure_kpa"] == pytest.approx(2.0)

    def test_amb_round_trip(self):
        data = build_amb(
            {
                "barometric_pressure_kpa": 99.5,
                "cab_interior_temp_c": 22.0,
                "ambient_air_temp_c": -18.5,
                "air_inlet_temp_c": 19,
                "road_surface_temp_c": 24.0,
            }
        )
        out = parse_amb(data)
        assert out["spn_108_barometric_pressure_kpa"] == pytest.approx(99.5)
        assert out["spn_170_cab_interior_temp_c"] == pytest.approx(22.0)
        assert out["spn_171_ambient_air_temp_c"] == pytest.approx(-18.5)
        assert out["spn_172_air_inlet_temp_c"] == 19
        assert out["spn_79_road_surface_temp_c"] == pytest.approx(24.0)

    def test_dd_round_trip(self):
        data = build_dd(
            {
                "washer_fluid_level_pct": 70,
                "fuel_level_pct": 64.4,
                "fuel_level2_pct": 51.2,
                "cargo_ambient_temp_c": 6.0,
                "seat_belt_fastened": True,
                "exterior_light_on": True,
                "maintenance_lamp_on": False,
            }
        )
        out = parse_dd(data)
        assert out["spn_80_washer_fluid_level_pct"] == pytest.approx(70.0)
        assert out["spn_96_fuel_level_pct"] == pytest.approx(64.4)
        assert out["spn_38_fuel_level2_pct"] == pytest.approx(51.2)
        assert out["spn_169_cargo_ambient_temp_c"] == pytest.approx(6.0)
        assert out["spn_1856_seat_belt_fastened"] == 1
        assert out["spn_1883_exterior_light_on"] == 1
        assert out["spn_1420_maintenance_lamp_on"] == 0

    def test_ccss_round_trip(self):
        out = parse_ccss(
            build_ccss(
                {
                    "cruise_high_limit_kmh": 95,
                    "cruise_low_limit_kmh": 30,
                    "max_speed_limit_kmh": 90,
                }
            )
        )
        assert out["spn_1085_cruise_high_limit_kmh"] == 95
        assert out["spn_1086_cruise_low_limit_kmh"] == 30
        assert out["spn_1087_max_vehicle_speed_limit_kmh"] == 90

    def test_ccvs1_carries_dbc_cruise_switches(self):
        out = parse_ccvs1(
            build_ccvs1(
                {
                    "speed_kmh": 60.0,
                    "cruise_active": True,
                    "cruise_enable": True,
                    "clutch_switch": True,
                    "cruise_set_switch": True,
                    "cruise_resume_switch": True,
                    "pto_state": 0,
                }
            )
        )
        assert out["spn_598_clutch_switch"] == 1
        assert out["spn_599_cruise_set_switch"] == 1
        assert out["spn_601_cruise_resume_switch"] == 1
        assert out["spn_600_cruise_coast_switch"] == 0
        assert out["spn_527_cruise_state"] == 4

    def test_hours_carries_pto_hours(self):
        out = parse_hours(build_hours({"engine_hours": 5321.4, "pto_hours": 118.25}))
        assert out["spn_247_engine_hours"] == pytest.approx(5321.4, abs=0.05)
        assert out["spn_248_pto_hours"] == pytest.approx(118.25, abs=0.05)

    def test_ebc1_carries_dbc_status_bits(self):
        out = parse_ebc1(
            build_ebc1(
                {
                    "brake_pedal_pct": 80.0,
                    "abs_active": True,
                    "total_brake_demand_pct": 80.0,
                    "trailer_connected": True,
                    "abs_fully_operational": True,
                    "brake_source_address": 11,
                }
            )
        )
        assert out["spn_563_abs_active"] == 1
        assert out["spn_2911_total_brake_demand_pct"] == pytest.approx(80.0)
        assert out["spn_1836_trailer_connected"] == 1
        assert out["spn_575_abs_fully_operational"] == 1
        assert out["spn_1481_brake_source_address"] == 11

    def test_eec2_carries_torque_availability(self):
        out = parse_eec2(
            build_eec2(
                {
                    "accel_pedal_pct": 36.4,
                    "engine_load_pct": 57,
                    "max_available_torque_pct": 95,
                    "parasitic_losses_pct": 12,
                }
            )
        )
        assert out["spn_539_max_available_torque_pct"] == 95
        assert out["spn_1481_parasitic_losses_pct"] == 12


class TestRegistry:
    def test_all_messages_registered(self):
        assert len(MESSAGES) == 19
        assert {m.acronym for m in MESSAGES.values()} == {
            "CCVS1",
            "CCSS",
            "EEC1",
            "EEC2",
            "ETC1",
            "ETC2",
            "EBC1",
            "EBC2",
            "LFE1",
            "ET1",
            "EFLP1",
            "IC1",
            "AMB",
            "DD",
            "HVBATT",
            "DM1",
            "VEP1",
            "HOURS",
            "VDHR",
        }

    def test_pgns_are_unique(self):
        """Ayni PGN'i iki mesaj paylasamaz: kayit defteri PGN ile anahtarlanir."""
        pgns = [m.pgn for m in MESSAGES.values()]
        assert len(pgns) == len(set(pgns))

    @pytest.mark.parametrize("pgn", list(MESSAGES))
    def test_every_message_builds_eight_bytes(self, pgn):
        frame = build_frame(pgn, {}, source_address=0x00)
        assert len(frame.data) == 8
        assert frame.to_dict()["dlc"] == 8
        assert len(frame.data_hex) == 16


class TestDm1:
    def test_no_fault_is_clear(self):
        decoded = parse_dm1(build_dm1({}))
        assert decoded["mil_lamp_on"] is False
        assert decoded["spn"] is None

    def test_fault_roundtrip(self):
        data = build_dm1({"dtc_spn": 110, "dtc_fmi": 0, "dtc_occurrence_count": 3})
        decoded = parse_dm1(data)
        assert decoded["mil_lamp_on"] is True
        assert decoded["spn"] == 110
        assert decoded["fmi"] == 0
        assert decoded["occurrence_count"] == 3
        assert decoded["spn_name"] == "Motor Sogutma Suyu Sicakligi"

    def test_frame_can_id_uses_dm1_pgn(self):
        frame = build_frame(PGN_DM1, {"dtc_spn": 110, "dtc_fmi": 0}, source_address=0x00)
        assert frame.can_id_hex == "18FECA00"

    def test_invalid_fmi_rejected(self):
        with pytest.raises(J1939Error):
            build_dm1({"dtc_spn": 110, "dtc_fmi": 99})


class TestVep1:
    @pytest.mark.parametrize("lat,lon", [(0.0, 0.0), (41.0082, 28.9784), (-33.8688, 151.2093)])
    def test_roundtrip_within_resolution(self, lat, lon):
        data = build_vep1({"latitude_deg": lat, "longitude_deg": lon})
        decoded = parse_vep1(data)
        assert decoded["spn_584_latitude_deg"] == pytest.approx(lat, abs=1e-6)
        assert decoded["spn_585_longitude_deg"] == pytest.approx(lon, abs=1e-6)

    def test_not_available(self):
        decoded = parse_vep1(build_vep1({}))
        assert decoded["spn_584_latitude_deg"] is None
        assert decoded["spn_585_longitude_deg"] is None


class TestHours:
    def test_roundtrip(self):
        data = build_hours({"engine_hours": 1234.55})
        assert parse_hours(data)["spn_247_engine_hours"] == pytest.approx(1234.55, abs=0.05)

    def test_not_available(self):
        assert parse_hours(build_hours({}))["spn_247_engine_hours"] is None


class TestVdhr:
    def test_roundtrip(self):
        data = build_vdhr({"trip_km": 12.345, "total_km": 98765.4})
        decoded = parse_vdhr(data)
        assert decoded["spn_917_trip_distance_km"] == pytest.approx(12.345, abs=0.005)
        assert decoded["spn_918_total_distance_km"] == pytest.approx(98765.4, abs=0.005)


class TestFrameGeneric:
    @pytest.mark.parametrize("pgn", list(MESSAGES))
    def test_every_message_parses_back(self, pgn):
        frame = build_frame(pgn, {}, source_address=0x00)
        assert isinstance(parse_frame(pgn, frame.data), dict)

    def test_unknown_pgn_rejected(self):
        with pytest.raises(J1939Error):
            build_frame(12345, {}, source_address=0)

    def test_transmit_rates(self):
        rates = {m.acronym: m.transmit_rate_ms for m in MESSAGES.values()}
        assert rates == {
            "CCVS1": 100,
            "CCSS": 5000,
            "EEC1": 20,
            "EEC2": 50,
            "ETC1": 20,
            "ETC2": 100,
            "EBC1": 100,
            "EBC2": 100,
            "LFE1": 100,
            "ET1": 1000,
            "EFLP1": 500,
            "IC1": 500,
            "AMB": 1000,
            "DD": 1000,
            "HVBATT": 1000,
            "DM1": 1000,
            "VEP1": 1000,
            "HOURS": 5000,
            "VDHR": 1000,
        }

    def test_candump_format(self):
        frame = build_frame(PGN_EEC2, {"accel_pedal_pct": 50}, 0x03)
        assert frame.candump.startswith("0CF00303#")
        assert frame.acronym == "EEC2"
