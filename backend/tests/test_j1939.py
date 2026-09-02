"""J1939 kodlama/cozme birim testleri."""

import pytest

from app.j1939 import (
    BIT2_ON,
    PGN_CCVS1,
    SPN84_MAX_KMH,
    SPN84_RAW_MAX,
    SPN84_RAW_NOT_AVAILABLE,
    J1939Error,
    build_can_id,
    build_ccvs1_data,
    build_ccvs1_frame,
    decode_can_id,
    decode_wheel_speed,
    encode_wheel_speed,
    pack_2bit,
    parse_ccvs1_data,
    unpack_2bit,
    wheel_speed_bytes,
)


class TestCanIdentifier:
    def test_ccvs1_id_matches_j1939_spec(self):
        assert build_can_id(PGN_CCVS1, source_address=0x00) == 0x18FEF100
        assert build_can_id(PGN_CCVS1, source_address=0x1D) == 0x18FEF11D

    def test_priority_shifts_top_bits(self):
        assert build_can_id(PGN_CCVS1, source_address=0, priority=3) == 0x0CFEF100
        assert build_can_id(PGN_CCVS1, source_address=0, priority=0) == 0x00FEF100

    def test_id_stays_within_29_bits(self):
        assert build_can_id(PGN_CCVS1, source_address=0xFF, priority=7) <= 0x1FFFFFFF

    def test_decode_roundtrip(self):
        decoded = decode_can_id(build_can_id(PGN_CCVS1, source_address=0x0A))
        assert decoded["pgn"] == PGN_CCVS1
        assert decoded["pdu_type"] == "PDU2"
        assert decoded["priority"] == 6
        assert decoded["source_address"] == 0x0A
        assert decoded["can_id_hex"] == "18FEF10A"

    def test_pdu1_uses_ps_as_destination(self):
        # PGN 59904 (0xEA00) request -> PF=0xEA (<240) yani PDU1
        can_id = build_can_id(59904, source_address=0x01, destination_address=0x21)
        assert decode_can_id(can_id)["destination_address"] == 0x21
        assert decode_can_id(can_id)["pdu_type"] == "PDU1"

    @pytest.mark.parametrize("priority", [-1, 8])
    def test_invalid_priority_rejected(self, priority):
        with pytest.raises(J1939Error):
            build_can_id(PGN_CCVS1, source_address=0, priority=priority)

    def test_invalid_source_address_rejected(self):
        with pytest.raises(J1939Error):
            build_can_id(PGN_CCVS1, source_address=256)


class TestSpn84:
    @pytest.mark.parametrize(
        "kmh,raw",
        [
            (0.0, 0x0000),
            (1.0, 0x0100),
            (60.0, 0x3C00),
            (90.0, 0x5A00),
            (100.0, 0x6400),
            (180.0, 0xB400),
            (250.996, SPN84_RAW_MAX),
        ],
    )
    def test_resolution_is_one_over_256(self, kmh, raw):
        assert encode_wheel_speed(kmh) == raw

    def test_little_endian_byte_order(self):
        # 60 km/h -> 15360 (0x3C00) -> dusuk byte 0x00, yuksek byte 0x3C
        assert wheel_speed_bytes(60.0) == (0x00, 0x3C)
        # 87.5 km/h -> 22400 (0x5780)
        assert wheel_speed_bytes(87.5) == (0x80, 0x57)

    def test_clamped_to_range(self):
        assert encode_wheel_speed(999) == SPN84_RAW_MAX
        assert encode_wheel_speed(-10) == 0x0000

    def test_not_available_marker(self):
        assert encode_wheel_speed(None) == SPN84_RAW_NOT_AVAILABLE
        assert decode_wheel_speed(SPN84_RAW_NOT_AVAILABLE) is None

    @pytest.mark.parametrize("kmh", [0, 12.5, 37.75, 88.0, 150.25, 180.0])
    def test_encode_decode_roundtrip(self, kmh):
        assert decode_wheel_speed(encode_wheel_speed(kmh)) == pytest.approx(kmh, abs=0.004)

    def test_max_value(self):
        assert SPN84_MAX_KMH == pytest.approx(250.996, abs=0.001)


class TestBitPacking:
    def test_pack_unpack_roundtrip(self):
        assert unpack_2bit(pack_2bit(0b01, 0b00, 0b11, 0b10)) == (0b01, 0b00, 0b11, 0b10)

    def test_bit_positions(self):
        assert pack_2bit(0b11, 0, 0, 0) == 0x03
        assert pack_2bit(0, 0, 0, 0b11) == 0xC0

    def test_out_of_range_rejected(self):
        with pytest.raises(J1939Error):
            pack_2bit(4, 0, 0, 0)


class TestCcvs1Message:
    def test_data_length_is_eight_bytes(self):
        assert len(build_ccvs1_data(50.0)) == 8

    def test_speed_lands_in_bytes_two_and_three(self):
        data = build_ccvs1_data(60.0)
        assert data[1] == 0x00 and data[2] == 0x3C

    def test_switch_states_are_reflected(self):
        data = build_ccvs1_data(0.0, parking_brake=BIT2_ON, brake_switch=BIT2_ON)
        signals = parse_ccvs1_data(data)
        assert signals["spn_70_parking_brake"] == BIT2_ON
        assert signals["spn_597_brake_switch"] == BIT2_ON

    def test_cruise_set_speed_byte_six(self):
        data = build_ccvs1_data(90.0, cruise_set_speed_kmh=88)
        assert data[5] == 88
        assert parse_ccvs1_data(data)["spn_86_cruise_set_speed_kmh"] == 88

    def test_cruise_set_speed_not_available_default(self):
        assert parse_ccvs1_data(build_ccvs1_data(10.0))["spn_86_cruise_set_speed_kmh"] is None

    def test_parse_rejects_wrong_length(self):
        with pytest.raises(J1939Error):
            parse_ccvs1_data(b"\x00\x01\x02")


class TestFrame:
    def test_candump_format(self):
        frame = build_ccvs1_frame(60.0, source_address=0x00)
        assert frame.candump.startswith("18FEF100#")
        assert len(frame.data_hex) == 16
        assert frame.candump == "18FEF100#F3003C0000FF1FFF"

    def test_frame_roundtrip_through_dict(self):
        frame = build_ccvs1_frame(75.5, source_address=0x07)
        payload = frame.to_dict()
        assert payload["pgn"] == PGN_CCVS1
        assert payload["dlc"] == 8
        assert payload["source_address"] == 0x07
        assert len(payload["data_bytes"]) == 8
        signals = parse_ccvs1_data(bytes.fromhex(payload["data_hex"]))
        assert signals["spn_84_wheel_based_speed_kmh"] == pytest.approx(75.5, abs=0.004)
