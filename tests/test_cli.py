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
    assert ret in (0, 1)


def test_cli_simulate_and_replay():
    """Verify simulate and replay commands roundtrip cleanly through pipeline."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        csv_out = str(tmp_path / "sim.csv")
        labels_out = str(tmp_path / "labels.csv")

        # Run simulate with --out
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

        # Run simulate with --output alias
        csv_out2 = str(tmp_path / "sim2.csv")
        ret_sim2 = main([
            "simulate",
            "--duration", "1.0",
            "--rate", "50.0",
            "--scenario", "bed_exit",
            "--output", csv_out2,
        ])
        assert ret_sim2 == 0
        assert Path(csv_out2).is_file()

        # Run replay through shared WiBreathePipeline
        ret_rep = main([
            "replay",
            "--file", csv_out,
            "--fast",
        ])
        assert ret_rep == 0


def test_cli_calibrate():
    """Verify calibrate command generates valid calibration JSON profile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        csv_out = str(tmp_path / "sim_empty.csv")
        labels_out = str(tmp_path / "labels_empty.csv")
        calib_out = str(tmp_path / "calib_empty.json")

        main([
            "simulate",
            "--scenario", "empty",
            "--duration", "3.0",
            "--out", csv_out,
            "--labels", labels_out,
        ])

        ret_cal = main([
            "calibrate",
            "--mode", "empty",
            "--file", csv_out,
            "--out", calib_out,
        ])
        assert ret_cal == 0
        assert Path(calib_out).is_file()


def test_cli_eval_and_evaluate_alias():
    """Verify eval and evaluate alias commands work identically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        ref_csv = tmp_path / "ref.csv"
        est_csv = tmp_path / "est.csv"
        out_dir = tmp_path / "results"

        with open(ref_csv, "w", newline="", encoding="utf-8") as f:
            f.write("start_time_s,end_time_s,state,target_bpm\n")
            f.write("0.0,30.0,STILL,16.0\n")

        with open(est_csv, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp_s,bpm,confidence,state\n")
            f.write("15.0,16.1,80.0,STILL\n")

        ret1 = main(["eval", "--est", str(est_csv), "--ref", str(ref_csv), "-o", str(out_dir)])
        assert ret1 == 0

        ret2 = main(["evaluate", "--est", str(est_csv), "--ref", str(ref_csv), "-o", str(out_dir)])
        assert ret2 == 0


if __name__ == "__main__":
    test_cli_help()
    test_cli_list_ports()
    test_cli_simulate_and_replay()
    test_cli_calibrate()
    test_cli_eval_and_evaluate_alias()
    print("All CLI tests passed.")
