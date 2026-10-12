import pandas as pd

from qpu_drift.sources.measurements.clean import (
    build_spine,
    device_epochs,
    explode_couplings,
    explode_qubits,
    parse_couplings,
    parse_qubits,
    target_type,
    valid_ids,
    validate_spine,
)
from qpu_drift.sources.measurements.load import local_payloads


def test_local_payloads_adds_prefix_and_first_root_wins(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, rel in [(a, "x/1.gz"), (b, "x/1.gz"), (b, "x/2.gz")]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"")
    got = local_payloads([(a, "results/"), (b, "results/"), (tmp_path / "missing", "")])
    assert got == {"results/x/1.gz": a / "x/1.gz", "results/x/2.gz": b / "x/2.gz"}


def test_parse_qubits_dedups_sorts_and_ignores_non_qubit_tags():
    assert parse_qubits("&Q4&&Q1&&Q4&") == [1, 4]
    assert parse_qubits("&C4-T5&") == []          # coupling token has no Q
    assert parse_qubits("&00&&01&&10&&11&") == []  # readout joint-state labels
    assert parse_qubits("") == []


def test_parse_qubits_recovers_malformed_double_q_tag():
    # the one dirty tag in the index, &QQ14& on 2 rows, still resolves to Q14
    assert parse_qubits("&QQ14&") == [14]


def test_parse_couplings_preserves_direction():
    assert parse_couplings("&C4-T5&") == ["C4-T5"]
    # reverse of a coupling is a distinct directed target, both are kept
    assert parse_couplings("&C23-T24&&C24-T23&") == ["C23-T24", "C24-T23"]
    assert parse_couplings("&Q1&") == []


def test_target_type_classifies_every_case():
    assert target_type(1, 0) == "qubit"
    assert target_type(0, 1) == "coupling"
    assert target_type(0, 0) == "none"
    assert target_type(1, 1) == "mixed"


def _raw():
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "measurement_name": ["ramsey_group1", "cr_u_C4-T5", "readout", "rabi"],
            "measurement_type": ["Ramsey", "CRUnitaryIZ", "ReadoutMatrix", "Rabi"],
            "time_start": [
                "2025-01-01 08:00:00", "2025-01-01 08:00:05",
                "2025-01-03 09:00:00", "2025-01-01 08:00:10",
            ],
            "time_end": [
                "2025-01-01 08:00:04", "2025-01-01 08:00:09",
                "2025-01-03 09:00:12", "2025-01-01 08:00:12",
            ],
            "relative_filepath": ["a.gz", "b.gz", "c.gz", "d.gz"],
            "data_keys": ["&Q1&&Q4&", "&C4-T5&", "&00&&01&&10&&11&", "&Q1&"],
        }
    )


def test_build_spine_is_lossless_and_typed():
    spine = build_spine(_raw())
    # one row in, one row out - nothing dropped
    assert len(spine) == 4
    # sorted by time; the readout row (latest) lands last
    assert list(spine["id"]) == [1, 2, 4, 3]
    assert spine["duration_s"].iloc[0] == 4.0
    assert list(spine["target_type"]) == ["qubit", "coupling", "qubit", "none"]
    assert spine.loc[spine["id"] == 1, "n_qubits"].item() == 2
    assert spine.loc[spine["id"] == 3, "target_type"].item() == "none"


def test_timestamps_are_left_naive():
    spine = build_spine(_raw())
    # server clock is UTC, but no tz is attached
    assert spine["time_start"].dt.tz is None


def test_explode_qubits_fans_out_multiqubit_rows():
    ex = explode_qubits(build_spine(_raw()))
    # Q1&Q4 -> 2 rows, Q1 -> 1 row, coupling/readout rows drop out
    assert len(ex) == 3
    assert sorted(ex.loc[ex["id"] == 1, "qubit"]) == [1, 4]
    assert ex["qubit"].dtype.kind == "i"


def test_explode_couplings_splits_control_and_target():
    ex = explode_couplings(build_spine(_raw()))
    assert len(ex) == 1
    row = ex.iloc[0]
    assert (row["control"], row["target"]) == (4, 5)
    assert row["coupling"] == "C4-T5"


def test_device_epochs_flags_only_large_global_gaps():
    ep = device_epochs(build_spine(_raw()), gap_hours=24.0)
    # single ~49h gap between the 2025-01-01 burst and the 2025-01-03 readout
    assert len(ep) == 1
    assert ep["gap_hours"].iloc[0] > 24.0
    assert ep["gap_start"].iloc[0] == pd.Timestamp("2025-01-01 08:00:10")


def test_validate_spine_all_clean_is_zero():
    checks = validate_spine(build_spine(_raw()))
    assert set(checks) == {
        "nat_time_start", "nat_time_end", "negative_duration",
        "duplicate_id", "mixed_target_type", "other_tier",
    }
    assert all(v == 0 for v in checks.values())


def test_validate_spine_catches_anomalies():
    raw = _raw()
    raw.loc[3, "id"] = 1                              # duplicate id
    raw.loc[3, "time_end"] = "2025-01-01 07:59:00"    # end < start -> negative
    raw.loc[2, "time_start"] = "not-a-timestamp"      # -> NaT
    checks = validate_spine(build_spine(raw))
    assert checks["duplicate_id"] == 1
    assert checks["negative_duration"] == 1
    assert checks["nat_time_start"] == 1


def _id_spine() -> pd.DataFrame:
    return pd.DataFrame({
        "id": [1, 2, 3],
        "measurement_type": ["T1", "T1", "MeasureBands"],
        "time_start": pd.to_datetime(["2025-10-28 22:11", "2025-06-05 22:00",
                                      "2025-10-28 22:30"]),
    })


def test_valid_ids_rejects_reused_and_missing_ids():
    run = pd.Timestamp("2025-10-28 22:00")
    ok = valid_ids([1, 2, 3, 99], [run] * 4, _id_spine())
    assert ok.tolist() == [True, False, True, False]  # 2 is ~145 days off, 99 isn't in the spine


def test_valid_ids_checks_type_only_when_given():
    run = pd.Timestamp("2025-10-28 22:00")
    ok = valid_ids([1, 3, 1], [run] * 3, _id_spine(), expected_type=["T1", "T1", None])
    assert ok.tolist() == [True, False, False]  # an unmapped metric matches no type


def test_valid_ids_tolerance_is_configurable():
    late = pd.Timestamp("2025-10-31 22:11")
    assert not valid_ids([1], [late], _id_spine())[0]
    assert valid_ids([1], [late], _id_spine(), tolerance=pd.Timedelta(days=7))[0]
