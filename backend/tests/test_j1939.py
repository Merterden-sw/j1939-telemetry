"""J1939 kodlama/cozme birim testleri."""

import pytest

from app.j1939 import (
    MESSAGES,
    PGN_CCVS1,
    PGN_EBC1,
    PGN_EEC2,
    PGN_ETC2,
    PGN_HVBATT,
    SPN84_MAX_KMH,
    J1939Error,
    build_can_id,
    build_ccvs1,
    build_ccvs1_frame,
    build_ebc1,
    build_eec2,
    build_etc2,
    build_frame,
    build_hvbatt,
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
    parse_ccvs1,
    parse_ebc1,
    parse_eec2,
    parse_etc2,
    parse_frame,
    parse_hvbatt,
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


class TestRegistry:
    def test_five_messages_registered(self):
        assert len(MESSAGES) == 5
        assert {m.acronym for m in MESSAGES.values()} == {"CCVS1", "EEC2", "ETC2", "EBC1", "HVBATT"}

    @pytest.mark.parametrize("pgn", list(MESSAGES))
    def test_every_message_builds_eight_bytes(self, pgn):
        frame = build_frame(pgn, {}, source_address=0x00)
        assert len(frame.data) == 8
        assert frame.to_dict()["dlc"] == 8
        assert len(frame.data_hex) == 16

    @pytest.mark.parametrize("pgn", list(MESSAGES))
    def test_every_message_parses_back(self, pgn):
        frame = build_frame(pgn, {}, source_address=0x00)
        assert isinstance(parse_frame(pgn, frame.data), dict)

    def test_unknown_pgn_rejected(self):
        with pytest.raises(J1939Error):
            build_frame(12345, {}, source_address=0)

    def test_transmit_rates(self):
        rates = {m.acronym: m.transmit_rate_ms for m in MESSAGES.values()}
        assert rates == {"CCVS1": 100, "EEC2": 50, "ETC2": 100, "EBC1": 100, "HVBATT": 1000}

    def test_candump_format(self):
        frame = build_frame(PGN_EEC2, {"accel_pedal_pct": 50}, 0x03)
        assert frame.candump.startswith("0CF00303#")
        assert frame.acronym == "EEC2"
