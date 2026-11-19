"""Unit tests for the reference tap logger across platforms."""

import csv
import sys
import tempfile
from pathlib import Path

# Add src to sys.path for direct execution
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wibreathe.eval.reference_tap import REFERENCE_CSV_HEADER, ReferenceTapLogger


def test_reference_tap_logger_save_and_format():
    """Verify ReferenceTapLogger outputs valid CSV format with LF and utf-8."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = Path(tmpdir) / "test_tap.csv"
        logger = ReferenceTapLogger(output_csv_path=csv_file, volunteer_code="V05")

        logger.log_event("SESSION_START", "Begin test")
        logger.log_event("BREATH", "Inhale peak 1")
        logger.log_event("MOVEMENT", "Hand twitch")
        logger.log_event("BED_EXIT", "Stepped out")
        logger.log_event("SESSION_END", "Finish test")

        assert len(logger.events) == 5
        logger.save()
        assert csv_file.is_file()

        with open(csv_file, "r", encoding="utf-8", newline="") as f:
            reader = list(csv.reader(f))
            assert reader[0] == REFERENCE_CSV_HEADER
            assert len(reader) == 6
            assert reader[1][2] == "SESSION_START"
            assert reader[2][2] == "BREATH"
            assert reader[3][2] == "MOVEMENT"
            assert reader[4][2] == "BED_EXIT"
            assert reader[5][2] == "SESSION_END"


if __name__ == "__main__":
    test_reference_tap_logger_save_and_format()
    print("Reference tap logger tests passed.")
