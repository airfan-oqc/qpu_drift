import numpy as np
import pandas as pd
import pytest

from qpu_drift.derived.run_map.clean import (
    assign_rows,
    attach_ds2,
    channel_qubits,
    differs,
    ds2_series,
    entity_rows,
    result_rows,
    run_row,
    run_window,
    snapshots_around,
    warned,
)
from qpu_drift.derived.run_map.constants import DS2_PATHS, DS2_TEMPLATES
from qpu_drift.sources.hardware_models.clean import _template

T = pd.Timestamp


def test_run_window_by_start_hour():
    assert run_window(T("2025-01-01 08:45")) == "morning"
    assert run_window(T("2025-01-01 19:05")) == "evening"
    assert run_window(T("2025-01-01 02:00")) == "other"


def test_snapshots_around_takes_last_at_or_before_and_first_after():
    snaps = pd.Series([T("2025-01-01 08:00"), T("2025-01-01 09:00"), T("2025-01-01 10:00")])
    starts = pd.Series([T("2025-01-01 07:00"), T("2025-01-01 09:00"), T("2025-01-01 09:30"),
                        T("2025-01-01 11:00")])
    before, after = snapshots_around(starts, snaps)
    assert before.isna().tolist() == [True, False, False, False]
    assert before.iloc[1] == T("2025-01-01 09:00")  # a snapshot at the start itself is before
    assert after.iloc[1] == T("2025-01-01 10:00")
    assert pd.isna(after.iloc[0])  # no before snapshot, so no after either
    assert pd.isna(after.iloc[3])


def test_warned_parses_1q_params_and_2q_couplings():
    w1 = [
        "Amplitude calibration out of spec for Q3 with N=4",
        "Fine-frequency calibration of Q5 out of spec with delay 0.5us",
        "Q7 pulse DRAG out of spec - resetting to zero",
        "Amplitude12 calibration out of spec: Q9 pulse amplitude is greater than 1!",
    ]
    assert warned(w1, "calibrate_1q") == {("3", "amp"), ("5", "freq"), ("7", "drag")}
    w2 = ["Reverting calibration for (4, 5) with N=2 due to high amplitude",
          "Amplitude too high on qubit pair (6, 5)"]
    assert warned(w2, "calibrate_2q") == {"4-5", "6-5"}
    assert warned(None, "calibrate_2q") is None


def test_result_rows_key_step_order_by_routine():
    cal = {"result_ids": {"Q1": {"ramsey": [10, 11], "freq": []}}}
    assert result_rows(cal, "calibrate_1q") == [("Q1", "ramsey", 10), ("Q1", "ramsey", 11)]
    two = {"result_ids": {"C4_T5": 20}}
    assert result_rows(two, "calibrate_2q") == [("C4_T5", None, 20)]
    bench = {"result_ids": {"t1": {"group1": 30}}}
    assert result_rows(bench, "benchmarking") == [("group1", "t1", 30)]
    assert run_row(bench, "benchmarking") == {"n_result_ids": 1, "n_warnings": pd.NA}


def test_entity_rows_1q_measured_warned_changed():
    oi = {
        "hw_parameters": {"old_freqs": {"Q1": 5.0e9, "Q2": 5.1e9},
                          "new_freqs": {"Q1": 5.0e9 + 1e4, "Q2": 5.1e9}},
        "result_ids": {"Q1": {"ramsey": [1], "freq": [2]}, "Q2": {"ramsey": [], "freq": []}},
        "warnings": ["Fine-frequency calibration of Q2 out of spec with delay 0.1us"],
    }
    df = entity_rows(oi, "calibrate_1q").set_index("entity")
    assert df.loc["1", "changed"] and not df.loc["2", "changed"]
    assert df.loc["1", "measured"] and not df.loc["2", "measured"]
    assert not df.loc["1", "warned"] and df.loc["2", "warned"]
    assert df.loc["1", "qubit"] == 1 and (df["param"] == "freq").all()


def test_entity_rows_1q_amp_measured_needs_the_amp_step():
    oi = {
        "hw_parameters": {"old_amps": {"Q1": 0.5, "Q2": 0.5}, "new_amps": {"Q1": 0.51, "Q2": 0.5}},
        "result_ids": {"Q1": {"rabi": [1], "amp": [2]}, "Q2": {"rabi": [3], "amp": []}},
        "warnings": [],
    }
    df = entity_rows(oi, "calibrate_1q").set_index("entity")
    assert df.loc["1", "measured"] and not df.loc["2", "measured"]


def test_entity_rows_2q_splits_complex_and_keeps_missing_new():
    oi = {
        "hw_parameters": {
            "old_cr_scale": {"C4_T5": {"type": "complex", "real": 1.0, "imag": -2.0}, "C6_T5": 0},
            "new_cr_scale": {"C4_T5": {"type": "complex", "real": 1.5, "imag": -2.0}},
        },
        "result_ids": {"C4_T5": 99},
        "warnings": [],
    }
    df = entity_rows(oi, "calibrate_2q").set_index(["entity", "param"])
    assert df.loc[("4-5", "cr_scale_re"), "new"] == 1.5
    assert df.loc[("4-5", "cr_scale_im"), "old"] == -2.0
    assert df.loc[("4-5", "cr_scale_re"), "changed"]
    assert not df.loc[("4-5", "cr_scale_im"), "changed"]
    assert pd.isna(df.loc[("6-5", "cr_scale_re"), "new"])
    assert pd.isna(df.loc[("6-5", "cr_scale_re"), "changed"])
    assert not df.loc[("6-5", "cr_scale_re"), "measured"]


def test_entity_rows_mixer_measured_is_na_when_ambiguous():
    oi = {
        "hw_parameters": {"old_imbalance": {"Ch1": 1.0, "Ch3": 1.0},
                          "new_imbalance": {"Ch1": 1.1, "Ch3": 1.0}},
        "result_ids": {"Q1": {"mixer_cal": [5, 6]}, "Q2": {"mixer_cal": [7]}},
    }
    df = entity_rows(oi, "mixer_calibration", {"Ch1": 1, "Ch3": 2}).set_index("entity")
    assert df.loc["Ch1", "measured"]
    assert pd.isna(df.loc["Ch3", "measured"])
    assert df["warned"].isna().all()


def _values(rows):
    cols = ["path", "entity", "entity_id", "dtype", "value_int", "value_float", "value_json",
            "valid_from_ts", "valid_to_ts"]
    return pd.DataFrame(rows, columns=cols)


def test_ds2_series_and_channel_qubits():
    v = _values([
        ["model.pulse_channels{Ch1.Q1.drive}.pulse_channel.frequency", "pulse_channels",
         "Ch1.Q1.drive", "float", np.nan, 5e9, None, T("2025-01-01"), pd.NaT],
        ["model.pulse_channels{Ch3.Q5.Q4.cross_resonance_cancellation}.pulse_channel.scale",
         "pulse_channels", "Ch3.Q5.Q4.cross_resonance_cancellation", "complex", np.nan, np.nan,
         "[0.5, -0.25]", T("2025-01-01"), pd.NaT],
        ["model.pulse_channels{Ch2.R1.measure}.pulse_channel.frequency", "pulse_channels",
         "Ch2.R1.measure", "float", np.nan, 7e9, None, T("2025-01-01"), pd.NaT],
    ])
    s = ds2_series(v).set_index(["param", "entity"])["value"]
    assert s[("freq", "1")] == 5e9
    assert s[("cancel_scale_re", "4-5")] == 0.5  # path is target first, entity is control-target
    assert s[("cancel_scale_im", "4-5")] == -0.25
    assert channel_qubits(v) == {"Ch1": 1, "Ch2": 1}


@pytest.mark.parametrize("path", [
    "model.pulse_channels{Ch1.Q1.drive}.pulse_channel.frequency",
    "model.quantum_devices{Q1}.pulse_hw_x_pi_2{amp}",
    "model.quantum_devices{Q1}.pulse_hw_x_pi_2{drag}",
    "model.quantum_devices{Q1}.pulse_hw_zx_pi_4{Q2}{amp}",
    "model.pulse_channels{Ch3.Q1.Q2.cross_resonance}.pulse_channel.scale",
    "model.pulse_channels{Ch4.Q2.Q1.cross_resonance_cancellation}.pulse_channel.scale",
    "model.physical_channels{Ch1}.dcbiaschannel_pair{I}._bias_value",
    "model.physical_channels{Ch1}.dcbiaschannel_pair{Q}._bias_value",
    "model.physical_channels{Ch1}.imbalance",
    "model.physical_channels{Ch1}.phase_offset",
])
def test_ds2_templates_cover_every_tracked_path(path):
    assert any(rx.fullmatch(path) for rx, _, _ in DS2_PATHS)
    assert _template(path) in DS2_TEMPLATES


def test_attach_ds2_reads_both_sides_and_respects_absence():
    runs = pd.DataFrame({
        "routine": ["calibrate_1q", "calibrate_1q"],
        "start": [T("2025-01-02 08:00"), T("2025-01-04 08:00")],
        "snap_before": [T("2025-01-02 07:58"), T("2025-01-04 07:58")],
        "snap_after": [T("2025-01-02 09:00"), T("2025-01-04 09:00")],
    })
    entities = pd.DataFrame({
        "routine": "calibrate_1q", "start": runs["start"], "param": "amp", "entity": "1",
    })
    series = pd.DataFrame({
        "routine": "calibrate_1q", "param": "amp", "entity": "1",
        "valid_from_ts": [T("2025-01-01"), T("2025-01-02 09:00")],
        "valid_to_ts": [T("2025-01-02 07:58"), T("2025-01-03")],  # absent from 2025-01-03 on
        "value": [0.1, 0.2],
    })
    out = attach_ds2(entities, runs, series)
    assert out.loc[0, ["before", "after"]].tolist() == [0.1, 0.2]
    assert bool(out.loc[0, "written"])
    assert out.loc[1, ["before", "after"]].isna().all()
    assert pd.isna(out.loc[1, "written"])


def test_differs_is_nullable():
    got = differs([1.0, 1.0, np.nan], [1.0, 2.0, 1.0])
    assert not got[0]
    assert got[1]
    assert pd.isna(got[2])


def test_assign_rows_uses_claims_then_run_spans():
    run = T("2025-01-01 08:45")
    results = pd.DataFrame({"routine": "calibrate_1q", "start": run,
                            "result_id": [1, 3, 5], "valid": [True, True, False]})
    spine = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "time_start": [T("2025-01-01 08:46"), T("2025-01-01 08:50"), T("2025-01-01 09:00"),
                       T("2025-01-01 12:00"), T("2025-01-01 08:47")],
        "time_end": [T("2025-01-01 08:47"), T("2025-01-01 08:51"), T("2025-01-01 09:01"),
                     T("2025-01-01 12:01"), T("2025-01-01 08:48")],
    })
    out = assign_rows(results, spine).set_index("id")
    assert out.index.tolist() == [1, 2, 3, 5]  # 4 is after the last claimed row
    assert out["claimed"].tolist() == [True, False, True, False]  # 5 is inside but not valid
    assert (out["start"] == run).all()
