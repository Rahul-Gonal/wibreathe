"""Golden-string and serialization tests for laptop->ESP32 OLED protocol."""

from wibreathe.io.display_link import format_disp_message, parse_disp_message
from wibreathe.state.machine import SystemState


def test_disp_message_golden_strings():
    """Verify exact golden strings for laptop->receiver OLED protocol."""
    # Test case 1: Steady breathing
    msg1 = format_disp_message(SystemState.STILL, 16.24, 85.2, 1)
    assert msg1 == "DISP,STILL,16.2,85,1"

    parsed1 = parse_disp_message(msg1)
    assert parsed1 == ("STILL", 16.2, 85, 1)

    # Test case 2: Movement without valid rate
    msg2 = format_disp_message(SystemState.MOVING, None, 0.0, 2)
    assert msg2 == "DISP,MOVING,--,0,2"

    parsed2 = parse_disp_message(msg2)
    assert parsed2 == ("MOVING", None, 0, 2)

    # Test case 3: Bed Exit
    msg3 = format_disp_message(SystemState.EXIT, None, 10.0, 42)
    assert msg3 == "DISP,EXIT,--,10,42"

    parsed3 = parse_disp_message(msg3)
    assert parsed3 == ("EXIT", None, 10, 42)

    # Test case 4: Boundary confidence clamping (out of range)
    msg4 = format_disp_message("CALIBRATING", None, 120.0, 5)
    assert msg4 == "DISP,CALIBRATING,--,100,5"

    parsed4 = parse_disp_message(msg4)
    assert parsed4 == ("CALIBRATING", None, 100, 5)


def test_disp_message_malformed_rejection():
    """Verify that corrupt or overlong downstream messages are rejected cleanly."""
    assert parse_disp_message("DISP:BPM=16.2") is None
    assert parse_disp_message("CSI_DATA,1,2,3") is None
    assert parse_disp_message("DISP,STILL,16.2") is None  # Missing fields
    assert parse_disp_message("") is None


if __name__ == "__main__":
    test_disp_message_golden_strings()
    test_disp_message_malformed_rejection()
    print("Protocol tests passed.")
