import numpy as np
import pandas as pd
import pytest

from qpu_drift.sources.fridge.build import _load_raw
from qpu_drift.sources.fridge.clean import (
    clean_wide,
    cold_epochs,
    cold_mask,
    collapse_replicas,
    metric_name,
    physical_metrics,
)


def _ts(n):
    return pd.date_range("2025-08-01", periods=n, freq="6h", tz="UTC")


def test_metric_name_extracts_prometheus_name():
    col = '{__name__="temperature_DRI_MIX_CL_DRI_MIX_S", pod="watchdog-lx2-abc"}'
    assert metric_name(col) == "temperature_DRI_MIX_CL_DRI_MIX_S"


def test_physical_metrics_keeps_physical_drops_infra():
    cols = [
        '{__name__="temperature_DRI_PT2_S", pod="a"}',
        '{__name__="pressure_OVC_PG_01", pod="a"}',
        '{__name__="threshold_speed_x", pod="a"}',
        '{__name__="Circulating_State", pod="a"}',
        '{__name__="operating_hours_x", pod="a"}',
        '{__name__="up", pod="a"}',
    ]
    groups = physical_metrics(cols)
    assert set(groups) == {"temperature_DRI_PT2_S", "pressure_OVC_PG_01"}


def test_collapse_newest_pod_wins_on_handover():
    idx = _ts(4)
    # old pod reports rows 0-1 then stops; new pod reports rows 1-3 (overlap @1)
    old = pd.Series([1.0, 1.0, np.nan, np.nan], index=idx)
    new = pd.Series([np.nan, 2.0, 2.0, 2.0], index=idx)
    block = pd.DataFrame({"old_pod": old, "new_pod": new})
    out = collapse_replicas(block)
    # overlap row takes the newer pod (2.0), never an average (1.5)
    assert list(out) == [1.0, 2.0, 2.0, 2.0]


def test_collapse_single_pod_rows_pass_through():
    idx = _ts(3)
    block = pd.DataFrame(
        {"a": [np.nan, 5.0, np.nan], "b": [7.0, np.nan, 9.0]}, index=idx
    )
    assert list(collapse_replicas(block)) == [7.0, 5.0, 9.0]


def test_cold_mask_drops_warmup_zero_hot_and_nan():
    idx = _ts(4)
    wide = pd.DataFrame(
        {
            "temperature_DRI_MIX_CL_DRI_MIX_S": [0.009, 0.0, 45.0, np.nan],
            "temperature_DRI_PT2_S": [3.9, 3.9, 299.0, 3.9],
        },
        index=idx,
    )
    assert list(cold_mask(wide)) == [True, False, False, False]


def test_cold_mask_missing_pt2_keeps_valid_mix_row():
    idx = _ts(2)
    wide = pd.DataFrame(
        {
            "temperature_DRI_MIX_CL_DRI_MIX_S": [0.009, 0.009],
            "temperature_DRI_PT2_S": [np.nan, 12.0],
        },
        index=idx,
    )
    assert list(cold_mask(wide)) == [True, False]


def test_cold_epochs_split_at_gaps_and_cycles():
    idx = pd.DatetimeIndex(
        ["2025-08-01 00:00", "2025-08-01 06:00", "2025-08-01 12:00", "2025-08-03 12:00"], tz="UTC"
    )
    wide = pd.DataFrame(
        {
            "temperature_DRI_MIX_CL_DRI_MIX_S": [0.009, 45.0, 0.009, 0.009],
            "temperature_DRI_PT2_S": [3.9, 299.0, 3.9, 3.9],
        },
        index=idx,
    )
    # the warm row is NA; the 48 h jump to the last row starts a new epoch
    assert cold_epochs(wide).tolist() == [0, pd.NA, 0, 1]


def test_clean_wide_temperature_zeros_become_nan():
    idx = _ts(4)
    mix = '{__name__="temperature_DRI_MIX_CL_DRI_MIX_S", container="oqc-watchdog", pod="a"}'
    pt2 = '{__name__="temperature_DRI_PT2_S", container="oqc-watchdog", pod="a"}'
    flow = '{__name__="flow_3CL_FM_01", container="oqc-watchdog", pod="a"}'
    df = pd.DataFrame(
        {
            "Time": idx.asi8 // 10**6,
            mix: [0.009, 0.010, 0.011, 0.012],
            pt2: [3.9, 0.0, 4.0, 4.1],
            flow: [1.0, 0.0, 2.0, 3.0],
        }
    )
    wide, catalog = clean_wide(df, idx)
    assert np.isnan(wide["temperature_DRI_PT2_S"].iloc[1])
    assert wide["flow_3CL_FM_01"].iloc[1] == 0.0  # zeros outside temperatures are kept
    zeros = catalog.set_index("metric")["zeros_to_nan"]
    assert zeros["temperature_DRI_PT2_S"] == 1 and zeros["flow_3CL_FM_01"] == 0


def _col(metric, container="oqc-watchdog"):
    return f'{{__name__="{metric}", container="{container}", pod="a"}}'


def _keep_rule_export():
    """20 rows, row 10 warm; one channel per keep-rule outcome."""
    n = 20
    idx = _ts(n)
    rng = np.random.default_rng(0)
    mix = 0.009 + 1e-4 * rng.random(n)
    pt2 = 3.9 + 0.01 * rng.random(n)
    mix[10], pt2[10] = 45.0, 299.0
    early = np.full(n, np.nan)
    early[:6] = rng.random(6)  # complete over 30% of the record, then stops
    sparse = np.full(n, np.nan)
    sparse[::4] = rng.random(5)  # 25% of rows, gappy inside its own window
    heater = np.zeros(n)
    heater[10] = 5.0  # only moves during the warm row
    df = pd.DataFrame(
        {
            "Time": idx.asi8 // 10**6,
            _col("temperature_DRI_MIX_CL_DRI_MIX_S"): mix,
            _col("temperature_DRI_PT2_S"): pt2,
            _col("flow_3CL_FM_01"): rng.random(n),
            _col("pressure_3SP_PG_01"): early,
            _col("pressure_OVC_PG_01"): sparse,
            _col("power_DRI_MIX_CL_DRI_MIX_H"): heater,
        }
    )
    return df, idx


def test_clean_wide_keep_rule_outcomes():
    df, idx = _keep_rule_export()
    wide, catalog = clean_wide(df, idx)
    reason = catalog.set_index("metric")["drop_reason"]
    # early-only but complete in its window: kept by the window rule
    assert "pressure_3SP_PG_01" in wide and pd.isna(reason["pressure_3SP_PG_01"])
    assert reason["pressure_OVC_PG_01"] == "sparse"
    # a warm-up spike must not rescue a channel that is flat when cold
    assert reason["power_DRI_MIX_CL_DRI_MIX_H"] == "flat_cold"
    assert "flow_3CL_FM_01" in wide


def test_clean_wide_min_density_zero_keeps_sparse_not_flat():
    df, idx = _keep_rule_export()
    wide, _ = clean_wide(df, idx, min_density=0)
    assert "pressure_OVC_PG_01" in wide
    assert "power_DRI_MIX_CL_DRI_MIX_H" not in wide


def test_clean_wide_ignores_istio_sidecar():
    df, idx = _keep_rule_export()
    flow = df[_col("flow_3CL_FM_01")]
    with_sidecar = df.assign(**{_col("flow_3CL_FM_01", "istio-proxy"): flow * 20})
    wide, catalog = clean_wide(with_sidecar, idx)
    assert wide["flow_3CL_FM_01"].equals(clean_wide(df, idx)[0]["flow_3CL_FM_01"])
    assert catalog.set_index("metric").loc["flow_3CL_FM_01", "n_replicas"] == 1


def test_clean_wide_warns_without_cold_mask_channels():
    df, idx = _keep_rule_export()
    df = df.drop(columns=[_col("temperature_DRI_MIX_CL_DRI_MIX_S")])
    with pytest.warns(UserWarning, match="cold-mask channel"):
        _, catalog = clean_wide(df, idx)
    # with every row treated as cold, the warm spike keeps the heater
    assert catalog.set_index("metric").loc["power_DRI_MIX_CL_DRI_MIX_H", "kept"]


def test_load_raw_rejects_duplicate_timestamps(tmp_path):
    ms = _ts(3).asi8 // 10**6
    path = tmp_path / "fridge_lx2_grafana_20250801.csv"
    pd.DataFrame({"Time": [ms[0], ms[1], ms[1]], _col("flow_3CL_FM_01"): [1, 2, 3]}).to_csv(
        path, index=False
    )
    with pytest.raises(ValueError, match="duplicate timestamp"):
        _load_raw(path)
