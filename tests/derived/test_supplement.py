import numpy as np
import pandas as pd

from qpu_drift.derived import supplement

LENGTHS = [1, 10, 20, 40, 80, 120, 160, 200]


def _array(a) -> dict:
    return {"type": "numpyarray", "list": np.asarray(a).tolist()}


def _rb1q_payload(mid: int, x0: float, seeds: int = 6, noise: float = 0.01) -> dict:
    """Build an RB1q group payload for Q1 with seed scatter and a FittingBase fit of the mean."""
    x = np.asarray(LENGTHS, dtype=float)
    rng = np.random.default_rng(0)
    raw = 0.8 * np.exp(-x / x0)[:, None] + 0.2 + noise * rng.standard_normal((len(x), seeds))
    y = raw.mean(1)
    a, fx0, b = supplement.fit_exponential(x, y, x0)
    return {
        "type": "Result",
        "name": "RandomizedBenchmarking1q",
        "dict": {
            "id": mid,
            "measurement_name": "rb1q_group1",
            "config": {"type": "Config", "dict": {"lengths": _array(LENGTHS), "seeds": seeds}},
            "data": {"Q1": {"type": "Data", "dict": {
                "raw_output": _array(raw),
                "fit": {"type": "FittingBase", "dict": {
                    "function_string": "y = A*exp(-x/x0) + B",
                    "optimised_results": [a, fx0, b],
                    "x_data": list(x), "y_data": list(y),
                    "r2": supplement.r_squared(x, y, a, fx0, b),
                }},
                "fidelity": float(supplement.rb_fidelity("RandomizedBenchmarking1q", fx0)),
                "fidelity_uncertainty": 1e-4,
            }}},
        },
    }


def _t1_payload(mid: int, qubits: dict[str, float]) -> dict:
    """Build a T1 group payload in the old Fit format (opt_vector, no stored r2)."""
    x = np.linspace(0, 200e-6, 21)
    data = {}
    for q, t1 in qubits.items():
        y = -1.0 * np.exp(-x / t1) + 1.0 + 0.01 * np.cos(x * 1e5)
        data[q] = {"type": "Data", "dict": {
            "raw_output": _array(y),
            "fit": {"type": "Fit", "dict": {
                "function_string": "y = A*exp(-x/x0) + B",
                "opt_vector": _array([-1.0, t1, 1.0]),
                "xdata": _array(x), "ydata": _array(y),
            }},
            "T1": t1, "T1_err": t1 * 0.02,
        }}
    return {"type": "Result", "name": "T1",
            "dict": {"id": mid, "measurement_name": "t1_group1", "config": {}, "data": data}}


def test_fit_exponential_recovers_parameters():
    x = np.asarray(LENGTHS, dtype=float)
    y = 0.7 * np.exp(-x / 55.0) + 0.25
    a, x0, b = supplement.fit_exponential(x, y, 30.0)
    assert np.isclose(a, 0.7) and np.isclose(x0, 55.0) and np.isclose(b, 0.25)


def test_fit_exponential_is_nan_without_an_interior_minimum():
    x = np.asarray(LENGTHS, dtype=float)
    assert np.isnan(supplement.fit_exponential(x, np.full(len(x), 0.5), 50.0)[1])
    assert np.isnan(supplement.fit_exponential(x, x * 0 + 1, -1.0)[1])


def test_jackknife_se_matches_the_formula():
    est = [1.0, 2.0, 3.0, 4.0]
    expected = np.sqrt(3 / 4 * sum((e - 2.5) ** 2 for e in est))
    assert np.isclose(supplement.jackknife_se(est), expected)
    assert np.isnan(supplement.jackknife_se([1.0, np.nan]))


def test_rb2q_seed_curves_average_to_the_producer_p00():
    rng = np.random.default_rng(1)
    raw = rng.choice([-1.0, 1.0], size=(3, 4, 2, 50))
    curves = supplement.rb_seed_curves("RandomizedBenchmarking2q", raw)
    p00 = [0.25 + (raw[j, :, 0].mean() + raw[j, :, 1].mean()
                   + (raw[j, :, 0] * raw[j, :, 1]).mean()) / 4 for j in range(3)]
    assert curves.shape == (3, 4)
    assert np.allclose(curves.mean(1), p00)


def test_payload_fit_rows_rb_has_seed_error_and_matching_checks():
    rows = supplement.payload_fit_rows(_rb1q_payload(2000, x0=60.0))
    row = rows[0]
    assert row["target"] == "Q1" and row["result_id"] == 2000
    assert row["curve_match"] is True and row["n_seeds"] == 6
    assert np.isclose(row["x0_value"], row["value"])
    assert np.isclose(row["refit_x0"], row["x0"], rtol=1e-6)
    assert np.isclose(row["payload_r2"], row["calc_r2"])
    assert 0 < row["seed_error"] < 0.01
    assert row["lengths"] == "[1, 10, 20, 40, 80, 120, 160, 200]"


def test_payload_fit_rows_old_fit_format_computes_r2():
    rows = {r["target"]: r for r in supplement.payload_fit_rows(_t1_payload(1000, {"Q1": 50e-6}))}
    q1 = rows["Q1"]
    assert q1["value"] == 50e-6 and np.isclose(q1["error"], 1e-6)
    assert np.isfinite(q1["payload_r2"]) and q1["payload_r2"] == q1["calc_r2"]
    assert np.isnan(q1["seed_error"]) and q1["lengths"] is None


def _kpi() -> pd.DataFrame:
    run1, run2 = pd.Timestamp("2025-09-01 23:00"), pd.Timestamp("2026-03-10 23:00")
    at1 = pd.Timestamp("2025-09-01 20:00")
    rows = [
        # T1 on Q1: DS3 has no error and no result_id; the group payload fills both
        (run1, "1", "T1", 49e-6, np.nan, pd.NA, at1),
        # T1 on Q4: absent from output_info rows (measured_at NaT) but in the run
        (run1, "4", "T1", np.nan, np.nan, pd.NA, pd.NaT),
        # fRB with DS3 error and its own id
        (run1, "1", "fRB", 0.99, 2e-4, 2000, at1),
        # fCX on a coupling whose last pointer is run1, measured again in run2
        (run1, "4-5", "fCX", 0.95, np.nan, 3000, at1),
        (run2, "4-5", "fCX", np.nan, np.nan, pd.NA, pd.NaT),
        (run2, "6-7", "fCX", 0.97, np.nan, pd.NA, pd.NaT),
        # last pointer is run1 too, but DS3 still serves a value in run2
        (run2, "8-9", "fCX", 0.96, np.nan, pd.NA, pd.NaT),
    ]
    kpi = pd.DataFrame(rows, columns=["created_at", "element", "metric", "value", "error",
                                      "result_id", "measured_at"])
    kpi["value_status"] = "ok"
    kpi["result_id"] = kpi["result_id"].astype("Int64")
    return kpi


def _links() -> pd.DataFrame:
    at1, at2 = pd.Timestamp("2025-09-01 20:00"), pd.Timestamp("2026-03-10 20:00")
    rows = [
        (at1, "T1", "group1", pd.NA, 1000, "a"),
        (at1, "fRB", "Q1", "1", 2000, "b"),
        (at1, "fCX", "C4-T5", "4-5", 3000, None),
        (at1, "fCX", "C6-T7", "6-7", 3001, None),
        (at2, "fCX", "C6-T7", "6-7", 3002, None),
        (at1, "fCX", "C8-T9", "8-9", 3003, None),
    ]
    links = pd.DataFrame(rows, columns=["measured_at", "metric", "key", "element",
                                        "result_id", "path"])
    links["guarded"] = True
    return links


def _fits() -> pd.DataFrame:
    t1 = supplement.payload_fit_rows(_t1_payload(1000, {"Q1": 50e-6, "Q4": 60e-6}))
    rb = supplement.payload_fit_rows(_rb1q_payload(2000, x0=60.0))
    fits = pd.DataFrame(t1 + rb)
    fits["metric"] = fits["measurement_type"].map({"T1": "T1", "RandomizedBenchmarking1q": "fRB"})
    fits["element"] = fits["target"].str.lstrip("Q")
    return fits


def test_supplement_kpi_links_groups_and_leaves_ds3_columns():
    kpi = _kpi()
    out = supplement.supplement_kpi(kpi, _links(), _fits()).set_index(["metric", "element", "created_at"])
    run1, run2 = pd.Timestamp("2025-09-01 23:00"), pd.Timestamp("2026-03-10 23:00")

    q1 = out.loc[("T1", "1", run1)]
    assert q1["payload_value"] == 50e-6 and q1["value"] == 49e-6
    assert q1["result_id"] == 1000 and q1["error_source"] == "payload"
    # a row with no output_info trace still reaches its group payload via the run
    assert out.loc[("T1", "4", run1), "payload_value"] == 60e-6

    frb = out.loc[("fRB", "1", run1)]
    assert frb["error_source"] == "ds3" and frb["result_id"] == 2000
    assert frb["rb_lengths"] == "[1, 10, 20, 40, 80, 120, 160, 200]"
    assert frb["seed_error"] > 0

    # 4-5's last pointer is run1, before the last pointer overall: retired in run2
    assert not out.loc[("fCX", "4-5", run1), "coupling_retired"]
    assert out.loc[("fCX", "4-5", run2), "coupling_retired"]
    assert not out.loc[("fCX", "6-7", run2), "coupling_retired"]
    # a DS3 value after the last pointer means the coupling is not retired
    assert not out.loc[("fCX", "8-9", run2), "coupling_retired"]
    assert out.loc[("fCX", "6-7", run2), "in_incident"]          # 2026-03 thermal cycle
    assert not out.loc[("fCX", "4-5", run1), "in_incident"]
    assert (out["value_status"] == "ok").all() and len(out) == len(kpi)


def test_supplement_kpi_is_idempotent():
    once = supplement.supplement_kpi(_kpi(), _links(), _fits())
    twice = supplement.supplement_kpi(once, _links(), _fits())
    pd.testing.assert_frame_equal(once, twice)
