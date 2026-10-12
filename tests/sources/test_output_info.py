import json

import pandas as pd

from qpu_drift.sources.output_info import iter_files, parse_run_path

T = pd.Timestamp


def test_parse_run_path_reads_start_and_routine():
    got = parse_run_path("root/2025/03/19/19_05_21_mixer_calibration.json")
    assert got == (T("2025-03-19 19:05:21"), "mixer_calibration")
    assert parse_run_path("root/2025/03/19/18_10_05_benchmarking.json") == (T("2025-03-19 18:10:05"), "benchmarking")
    assert parse_run_path("root/2025/03/19/notes.json") is None


def test_iter_files_filters_routines_and_skips_unreadable(tmp_path, capsys):
    day = tmp_path / "2025" / "03" / "19"
    day.mkdir(parents=True)
    (day / "08_45_16_calibrate_1q.json").write_text(json.dumps({"a": 1}))
    (day / "18_10_05_benchmarking.json").write_text(json.dumps({"b": 2}))
    (day / "19_00_00_benchmarking.json").write_text("{not json")
    (day / "20_00_00_benchmarking.json").write_text(json.dumps([1, 2]))
    (day / "notes.json").write_text("{}")

    got = [(p.name, start, routine, oi) for p, start, routine, oi in iter_files(tmp_path, ("benchmarking",))]
    assert got == [
        ("18_10_05_benchmarking.json", T("2025-03-19 18:10:05"), "benchmarking", {"b": 2}),
        ("20_00_00_benchmarking.json", T("2025-03-19 20:00:00"), "benchmarking", {}),
    ]
    assert "skipped 1 unreadable" in capsys.readouterr().err
    assert [r for _, _, r, _ in iter_files(tmp_path)] == ["calibrate_1q", "benchmarking", "benchmarking"]
