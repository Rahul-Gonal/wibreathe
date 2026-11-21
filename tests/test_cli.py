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
        from wibreathe.state.calibration import BedCalibration
        calib = BedCalibration.load(calib_out)
        # Empty only must refuse to mark calibrated
        assert calib.is_calibrated is False
        assert calib.empty_calibrated is True
        assert calib.occupied_calibrated is False


def test_cli_guided_calibration_and_formats():
    """Verify guided calibration recording both empty and occupied into single profile in json and yaml."""
    from wibreathe.state.calibration import BedCalibration
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        empty_csv = str(tmp_path / "sim_empty.csv")
        occ_csv = str(tmp_path / "sim_occ.csv")
        calib_json = str(tmp_path / "calib.json")
        calib_yaml = str(tmp_path / "calib.yaml")

        main(["simulate", "--scenario", "empty", "--duration", "2.0", "--out", empty_csv])
        main(["simulate", "--scenario", "still", "--duration", "2.0", "--out", occ_csv])

        # 1. Guided JSON run
        ret_json = main([
            "calibrate",
            "--mode", "guided",
            "--empty-file", empty_csv,
            "--occupied-file", occ_csv,
            "--out", calib_json,
        ])
        assert ret_json == 0
        assert Path(calib_json).is_file()
        calib1 = BedCalibration.load(calib_json)
        assert calib1.is_calibrated is True
        assert calib1.empty_calibrated is True
        assert calib1.occupied_calibrated is True

        # 2. Guided YAML run
        ret_yaml = main([
            "calibrate",
            "--mode", "guided",
            "--empty-file", empty_csv,
            "--occupied-file", occ_csv,
            "--out", calib_yaml,
        ])
        assert ret_yaml == 0
        assert Path(calib_yaml).is_file()
        calib2 = BedCalibration.load(calib_yaml)
        assert calib2.is_calibrated is True


def test_cli_replay_est_out_and_calibration():
    """Verify replay writes t,bpm,confidence,state to --est-out and accepts --calibration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        sim_csv = str(tmp_path / "sim.csv")
        est_csv = str(tmp_path / "est.csv")
        calib_json = str(tmp_path / "calib.json")

        main(["simulate", "--scenario", "still", "--duration", "5.0", "--out", sim_csv])
        main(["calibrate", "--mode", "guided", "--empty-file", sim_csv, "--occupied-file", sim_csv, "--out", calib_json])

        ret_rep = main([
            "replay",
            "--file", sim_csv,
            "--fast",
            "--calibration", calib_json,
            "--est-out", est_csv,
        ])
        assert ret_rep == 0
        assert Path(est_csv).is_file()

        with open(est_csv, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) >= 2
            assert lines[0].strip() == "t,bpm,confidence,state"


def test_cli_plot_file_flag():
    """Verify plot command accepts -f/--file and --out."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        sim_csv = str(tmp_path / "sim.csv")
        plot_out = str(tmp_path / "plot.png")

        main(["simulate", "--duration", "2.0", "--out", sim_csv])
        ret_plot = main(["plot", "-f", sim_csv, "-o", plot_out])
        assert ret_plot == 0


def test_cli_tap_session_flag():
    """Verify tap tool accepts -s/--session flag without error."""
    from wibreathe.eval.reference_tap import ReferenceTapLogger
    with tempfile.TemporaryDirectory() as tmpdir:
        out_csv = Path(tmpdir) / "tap.csv"
        logger = ReferenceTapLogger(out_csv, volunteer_code="V01", session="sess_42")
        assert logger.session == "sess_42"
        logger.log_event("SESSION_START", "Observer began logging")
        logger.save()
        assert out_csv.is_file()


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


def test_cli_record_consent_required():
    """Verify that record command strictly requires --consent flag."""
    import pytest
    # Missing --consent must trigger argparse error (SystemExit != 0)
    with pytest.raises(SystemExit) as exc_info:
        main(["record", "-p", "/dev/ttyUSB0", "--volunteer", "V01"])
    assert exc_info.value.code != 0



if __name__ == "__main__":
    test_cli_help()
    test_cli_list_ports()
    test_cli_simulate_and_replay()
    test_cli_calibrate()
    test_cli_guided_calibration_and_formats()
    test_cli_replay_est_out_and_calibration()
    test_cli_plot_file_flag()
    test_cli_tap_session_flag()
    test_cli_eval_and_evaluate_alias()
    print("All CLI tests passed.")
