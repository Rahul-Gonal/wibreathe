"""Unit tests for WiBreathe CLI commands."""

import sys
import tempfile
from pathlib import Path
from wibreathe.cli import main


def test_cli_help():
    """Verify that --help exits cleanly."""
    try:
        main(["--help"])
    except SystemExit as e:
        assert e.code == 0


def test_cli_list_ports():
    """Verify that list-ports runs without error."""
    ret = main(["list-ports"])
    # Returns 0 if ports found, 1 if no hardware ports discovered on headless machine
    assert ret in (0, 1)


def test_cli_simulate_and_replay():
    """Verify simulate and replay commands roundtrip cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        csv_out = str(tmp_path / "sim.csv")
        labels_out = str(tmp_path / "labels.csv")

        # Run simulate
        ret_sim = main([
            "simulate",
            "--duration", "3.0",
            "--rate", "50.0",
            "--out", csv_out,
            "--labels", labels_out,
        ])
        assert ret_sim == 0
        assert Path(csv_out).is_file()
        assert Path(labels_out).is_file()

        # Run replay
        ret_rep = main([
            "replay",
            "--file", csv_out,
            "--fast",
        ])
        assert ret_rep == 0


if __name__ == "__main__":
    test_cli_help()
    test_cli_list_ports()
    test_cli_simulate_and_replay()
    print("CLI tests passed.")
