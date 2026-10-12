import gzip
import json

import pandas as pd

from qpu_drift.sources.benchmarking import output_info, recover


def _oi_with_result_ids() -> dict:
    return {
        "result_ids": {
            "t1": {"group1": 1000, "group2": 1001},
            "t2e": {"group1": 1002, "group2": 1003},
            "rb1q": {"Q1": 2000, "Q4": 2001},
            "rb2q": {"C4-T5": 3000},
            "zmap": {"group1": 4000, "group2": 4001},
        }
    }


def _t1_group_payload(mid: int, qubits: dict[str, float]) -> dict:
    """Build a T1 group run where each qubit leaf has T1, T1_err and a fit r2."""
    return {
        "type": "Result",
        "name": "T1",
        "dict": {
            "id": mid,
            "measurement_name": "t1_group",
            "time_start": "2025-08-22 20:14:50.0",
            "data": {
                q: {
                    "type": "Data",
                    "dict": {
                        "T1": value,
                        "T1_err": value * 0.01,
                        "fit": {"type": "FittingBase", "dict": {"r2": 0.97}},
                    },
                }
                for q, value in qubits.items()
            },
        },
    }


def _rb2q_payload(mid: int, coupling: str, fidelity: float) -> dict:
    return {
        "type": "Result",
        "name": "RandomizedBenchmarking2q",
        "dict": {
            "id": mid,
            "measurement_name": f"rb2q_{coupling}",
            "time_start": "2025-08-22 21:56:14.0",
            "data": {
                coupling: {
                    "type": "Data",
                    "dict": {
                        "fidelity": fidelity,
                        "fidelity_uncertainty": 0.002,
                    },
                }
            },
        },
    }


def _write_gz(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def test_output_info_result_ids_surfaces_group_and_element():
    at = pd.Timestamp("2025-08-22 20:14:50")
    rows = output_info.output_info_result_ids(_oi_with_result_ids(), at)
    frame = pd.DataFrame(rows)

    # group-keyed T1 is surfaced with element NA; element-keyed fRB is normalised
    t1 = frame[frame["metric"] == "T1"]
    assert set(t1["key"]) == {"group1", "group2"}
    assert t1["element"].isna().all()
    assert set(t1["result_id"]) == {1000, 1001}

    frb = frame[frame["metric"] == "fRB"].set_index("element")
    assert frb.loc["1", "result_id"] == 2000
    assert frb.loc["4", "result_id"] == 2001

    fcx = frame[frame["metric"] == "fCX"]
    assert fcx.iloc[0]["element"] == "4-5" and fcx.iloc[0]["result_id"] == 3000


def test_ds1_candidates_expands_groups_and_keeps_element():
    at = pd.Timestamp("2025-08-22 20:14:50")
    kpi = pd.DataFrame([
        # a T1 (group-keyed, result_id null) and an fCX (element-keyed) ds1 cell
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "T1",
         "element": "1", "measured_at": at, "result_id": pd.NA, "value_source": "ds1"},
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "fCX",
         "element": "4-5", "measured_at": at, "result_id": 3000, "value_source": "ds1"},
        # a non-ds1 cell must be ignored
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "T1",
         "element": "4", "measured_at": at, "result_id": pd.NA, "value_source": "qcaas"},
    ])
    oi_ids = pd.DataFrame(output_info.output_info_result_ids(_oi_with_result_ids(), at))

    cand = recover.ds1_candidates(kpi, oi_ids)
    # T1 fans out to both group ids, fCX keeps its own, qcaas is dropped
    t1 = cand[cand["metric"] == "T1"]
    assert set(t1["result_id"]) == {1000, 1001}
    assert (t1["element"] == "1").all()
    fcx = cand[cand["metric"] == "fCX"]
    assert list(fcx["result_id"]) == [3000]
    assert cand["result_id"].dtype == "int64"


def test_resolve_and_guard_rejects_wrong_type_and_stale_time():
    at = pd.Timestamp("2025-08-22 20:14:50")
    candidates = pd.DataFrame([
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "T1",
         "element": "1", "measured_at": at, "result_id": 1000},   # good
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "T1",
         "element": "9", "measured_at": at, "result_id": 5000},   # wrong type
        {"created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "T1",
         "element": "7", "measured_at": at, "result_id": 6000},   # stale time
    ])
    spine = pd.DataFrame([
        {"id": 1000, "measurement_type": "T1",
         "time_start": pd.Timestamp("2025-08-22 20:15:00"),
         "relative_filepath": "results/2025/08/22/t1_group1.gz"},
        {"id": 5000, "measurement_type": "MeasureBands",         # not a T1 producer
         "time_start": pd.Timestamp("2025-08-22 20:15:00"),
         "relative_filepath": "results/x.gz"},
        {"id": 6000, "measurement_type": "T1",
         "time_start": pd.Timestamp("2025-02-01 00:00:00"),      # ~200 days off
         "relative_filepath": "results/y.gz"},
    ])
    guarded = recover.resolve_and_guard(candidates, spine)
    assert list(guarded["result_id"]) == [1000]
    assert "id" not in guarded.columns


def test_decode_values_selects_right_group(tmp_path):
    # the wrong-group candidate for element 1 finds no target and drops
    g1 = "results/2025/08/22/t1_group1.gz"
    g2 = "results/2025/08/22/t1_group2.gz"
    _write_gz(tmp_path / g1, _t1_group_payload(1000, {"Q1": 50e-6, "Q4": 60e-6}))
    _write_gz(tmp_path / g2, _t1_group_payload(1001, {"Q7": 70e-6}))

    at = pd.Timestamp("2025-08-22 20:14:50")
    t0 = pd.Timestamp("2025-08-22 23:00")
    manifest = pd.DataFrame([
        {"created_at": t0, "metric": "T1", "element": "1", "measured_at": at,
         "result_id": 1000, "measurement_type": "T1", "relative_filepath": g1},
        {"created_at": t0, "metric": "T1", "element": "1", "measured_at": at,
         "result_id": 1001, "measurement_type": "T1", "relative_filepath": g2},
        {"created_at": t0, "metric": "T1", "element": "4", "measured_at": at,
         "result_id": 1000, "measurement_type": "T1", "relative_filepath": g1},
    ])
    values = recover.decode_values(manifest, tmp_path)

    got = values.set_index("element")
    assert got.loc["1", "value"] == 50e-6
    assert got.loc["4", "value"] == 60e-6
    # element 1 resolves via group1 only
    assert (values["element"] == "1").sum() == 1
    assert got.loc["1", "result_id"] == 1000


def test_decode_values_recovers_coupling_fidelity(tmp_path):
    rel = "results/2025/08/22/rb2q_C4-T5.gz"
    _write_gz(tmp_path / rel, _rb2q_payload(3000, "C4-T5", 0.982))
    manifest = pd.DataFrame([{
        "created_at": pd.Timestamp("2025-08-22 23:00"), "metric": "fCX",
        "element": "4-5", "measured_at": pd.Timestamp("2025-08-22 20:14:50"),
        "result_id": 3000, "measurement_type": "RandomizedBenchmarking2q",
        "relative_filepath": rel,
    }])
    values = recover.decode_values(manifest, tmp_path)
    assert values.iloc[0]["value"] == 0.982
    assert values.iloc[0]["error"] == 0.002


def test_fill_kpi_only_fills_null_ds1_cells():
    t0 = pd.Timestamp("2025-08-22 23:00")
    kpi = pd.DataFrame([
        {"created_at": t0, "metric": "T1", "element": "1",
         "value": float("nan"), "error": float("nan"), "value_source": "ds1",
         "result_id": pd.NA},
        {"created_at": t0, "metric": "T1", "element": "2",
         "value": 55e-6, "error": 1e-6, "value_source": "qcaas",
         "result_id": pd.NA},
        # an fRB reject that already carries its per-element result_id
        {"created_at": t0, "metric": "fRB", "element": "3",
         "value": float("nan"), "error": float("nan"), "value_source": "ds1",
         "result_id": 500},
    ])
    values = pd.DataFrame([
        {"created_at": t0, "metric": "T1", "element": "1",
         "value": 50e-6, "error": 0.5e-6, "r2": 0.97, "result_id": 443968},
        # a value for the qcaas cell must not overwrite it
        {"created_at": t0, "metric": "T1", "element": "2",
         "value": 99e-6, "error": 9e-6, "r2": 0.97, "result_id": 443969},
        {"created_at": t0, "metric": "fRB", "element": "3",
         "value": 0.99, "error": 0.01, "r2": 0.97, "result_id": 999},
    ])
    filled = recover.fill_kpi(kpi, values)
    ds1 = filled[(filled["metric"] == "T1") & (filled["element"] == "1")].iloc[0]
    qcaas = filled[filled["value_source"] == "qcaas"].iloc[0]
    frb = filled[filled["metric"] == "fRB"].iloc[0]
    assert ds1["value"] == 50e-6 and ds1["error"] == 0.5e-6
    # the group-keyed T1 id is backfilled from the decode
    assert ds1["result_id"] == 443968
    assert qcaas["value"] == 55e-6 and qcaas["error"] == 1e-6  # untouched
    assert pd.isna(qcaas["result_id"])                         # untouched
    # fRB value is filled but its own id is kept
    assert frb["value"] == 0.99 and frb["result_id"] == 500
