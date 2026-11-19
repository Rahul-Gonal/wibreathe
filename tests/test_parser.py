"""Unit tests for CSIParser and timestamp unwrapping."""

import math

try:
    import pytest
except ImportError:
    class _PytestMock:
        class raises:
            def __init__(self, exc_type):
                self.exc_type = exc_type
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                if exc_type is None:
                    raise AssertionError(f"Expected {self.exc_type} was not raised")
                return issubclass(exc_type, self.exc_type)
    pytest = _PytestMock()  # type: ignore

from wibreathe.io.parser import CSIParser, CSIFrame


def test_parse_valid_25_column_line():
    parser = CSIParser()
    raw_array = [-10, 20, 15, -25]  # len = 4 -> 2 subcarriers
    line = (
        f"CSI_DATA,101,1a:00:00:00:00:00,-50,12,0,0,0,0,0,0,0,0,0,-90,0,6,0,1000000,0,0,0,4,0,"
        f"\"[{','.join(str(x) for x in raw_array)}]\""
    )

    frame = parser.parse_line(line)
    assert frame is not None
    assert frame.packet_id == 101
    assert frame.mac == "1a:00:00:00:00:00"
    assert frame.rssi == -50
    assert frame.channel == 6
    assert frame.noise_floor == -90
    assert frame.subcarrier_len == 2

    # Verify ground-truth IQ ordering:
    # index 0 is Q (-10), index 1 is I (20) -> complex(20, -10)
    assert frame.complex_csi[0] == complex(20.0, -10.0)
    assert math.isclose(frame.amplitudes[0], math.sqrt(20.0**2 + (-10.0)**2))

    # index 2 is Q (15), index 3 is I (-25) -> complex(-25, 15)
    assert frame.complex_csi[1] == complex(-25.0, 15.0)


def test_parse_mac_filter():
    parser = CSIParser(filter_mac="aa:bb:cc:dd:ee:ff")
    raw_array = [10, 20]
    line_reject = (
        f"CSI_DATA,1,1a:00:00:00:00:00,-50,12,0,0,0,0,0,0,0,0,0,-90,0,6,0,1000000,0,0,0,2,0,"
        f"\"[{','.join(str(x) for x in raw_array)}]\""
    )
    line_accept = (
        f"CSI_DATA,2,aa:bb:cc:dd:ee:ff,-50,12,0,0,0,0,0,0,0,0,0,-90,0,6,0,1000000,0,0,0,2,0,"
        f"\"[{','.join(str(x) for x in raw_array)}]\""
    )

    assert parser.parse_line(line_reject) is None
    assert parser.mac_filtered_count == 1

    accepted_frame = parser.parse_line(line_accept)
    assert accepted_frame is not None
    assert accepted_frame.packet_id == 2


def test_parse_length_mismatch_dropped():
    parser = CSIParser()
    # Declares len 4, but array has 2 elements
    line = (
        "CSI_DATA,10,1a:00:00:00:00:00,-50,12,0,0,0,0,0,0,0,0,0,-90,0,6,0,1000000,0,0,0,4,0,\"[10,20]\""
    )
    frame = parser.parse_line(line)
    assert frame is None
    assert parser.dropped_frames_count == 1


def test_parse_non_csi_lines_ignored():
    parser = CSIParser()
    assert parser.parse_line("STATUS,12000,500,0,6,none") is None
    assert parser.parse_line("I (450) wifi: Connected to AP") is None
    assert parser.parse_line("") is None


def test_signed_int8_conversion():
    parser = CSIParser()
    # 240 in unsigned int8 represents -16 in signed int8
    line = (
        "CSI_DATA,1,1a:00:00:00:00:00,-50,12,0,0,0,0,0,0,0,0,0,-90,0,6,0,1000000,0,0,0,2,0,\"[240,15]\""
    )
    frame = parser.parse_line(line)
    assert frame is not None
    assert frame.raw_bytes[0] == -16
    assert frame.raw_bytes[1] == 15
    # Complex is I (15) + Q (-16)*j
    assert frame.complex_csi[0] == complex(15.0, -16.0)


def test_timestamp_unwrapping_over_wraparound():
    parser = CSIParser()
    # Initial timestamp near end of 32-bit range: 4,294,960,000 us (~4294.96 s)
    t1 = 4_294_960_000
    s1 = parser.unwrap_timestamp(t1)
    assert math.isclose(s1, t1 / 1_000_000.0)

    # Wrap across boundary to 10,000 us (+17,296 us elapsed)
    t2 = 10_000
    s2 = parser.unwrap_timestamp(t2)
    delta_s = s2 - s1
    expected_delta_s = ((t2 - t1) % (1 << 32)) / 1_000_000.0
    assert math.isclose(delta_s, expected_delta_s, rel_tol=1e-6)
    assert s2 > s1
