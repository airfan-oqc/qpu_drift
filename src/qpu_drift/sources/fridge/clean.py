import re
import warnings

import numpy as np
import pandas as pd

from .constants import (
    MIN_DENSITY,
    MIX_CHANNEL,
    PT2_CHANNEL,
    WINDOW_MIN_DENSITY,
    WINDOW_MIN_SPAN,
)

KEEP_RE = re.compile(  # continuous physical sensors; sparse/flat ones are dropped later by the keep rule
    r"^(temperature_|bearing_temperature_|electronics_temperature_|"
    r"motor_casing_temperature_|oil_temperature_|power_stage_temperature_|"
    r"pump_casing_temperature_|inlet_water_temperature_|outlet_water_temperature_|"
    r"pressure_|high_side_pressure_|low_side_pressure_|flow_|speed_|"
    r"motor_power_|motor_current_|power_DRI|power_SRB|PSU_Voltage|UPS_Voltage)"
)
DROP_RE = re.compile(
    r"(threshold_|operating_hours_|power_enabled_|_info$|^up$|GRAFANA_ALERTS|"
    r"state|process_|python_|^go_|container_|scrape_|istio|http_|grpc_)",
    re.IGNORECASE,
)

_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")  # sorts before any real reading


def _labels(col: str) -> dict[str, str]:
    """Parse a Prometheus label-set column header into a {label: value} dict."""
    return dict(_LABEL_RE.findall(col))


def metric_name(col: str) -> str:
    """Return the Prometheus metric name from a label-set column header."""
    return _labels(col).get("__name__", col)


def physical_metrics(columns) -> dict[str, list[str]]:
    """Group raw columns by metric name, keeping only physical-sensor families."""
    # skip istio-proxy sidecars: same __name__, but values can diverge from oqc-watchdog
    keep: dict[str, list[str]] = {}
    for col in columns:
        labels = _labels(col)
        if labels.get("container") == "istio-proxy":
            continue
        metric = labels.get("__name__", col)
        if KEEP_RE.match(metric) and not DROP_RE.search(metric):
            keep.setdefault(metric, []).append(col)
    return keep


def collapse_replicas(block: pd.DataFrame) -> pd.Series:
    """Merge a metric's replica columns into one series; newest replica wins on overlap."""
    # averaging would invent a midpoint where a stale and a live pod disagree
    def last_valid(col: pd.Series) -> pd.Timestamp:
        valid = col.dropna()
        return valid.index.max() if not valid.empty else _EPOCH

    order = sorted(block.columns, key=lambda c: last_valid(block[c]), reverse=True)
    out = pd.Series(np.nan, index=block.index)
    for c in order:  # newest first; older replicas only fill remaining gaps
        out = out.combine_first(block[c])
    return out


def clean_wide(
    df: pd.DataFrame, timestamp: pd.DatetimeIndex, min_density: float = MIN_DENSITY
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the clean wide table and the per-channel catalog from the raw export."""
    # column 0 is time; the catalog covers every physical candidate, kept or dropped
    groups = physical_metrics(df.columns[1:])

    # collapse all candidates first so the cold mask and the stats share them
    series_by_metric: dict[str, pd.Series] = {}
    zeros_by_metric: dict[str, int] = {}
    for metric, cols in sorted(groups.items()):
        block = df[cols].apply(pd.to_numeric, errors="coerce")
        block.index = timestamp
        series = collapse_replicas(block).sort_index()
        # a temperature of exactly 0 is an exporter artefact, not a reading
        zeros = series == 0 if "temperature" in metric else pd.Series(False, index=series.index)
        zeros_by_metric[metric] = int(zeros.sum())
        series_by_metric[metric] = series.mask(zeros)

    candidates = pd.DataFrame(series_by_metric)
    # judge variance on cold rows; fall back to all rows if stage channels are missing
    missing = {MIX_CHANNEL, PT2_CHANNEL} - set(candidates.columns)
    if not missing:
        cold = cold_mask(candidates)
    else:
        warnings.warn(
            f"cold-mask channel(s) {sorted(missing)} not in export; "
            "judging flat_cold on all rows, so warm-up spikes can keep flat channels",
            stacklevel=2,
        )
        cold = pd.Series(True, index=candidates.index)

    kept: dict[str, pd.Series] = {}
    catalog_rows = []
    for metric, cols in sorted(groups.items()):
        series = series_by_metric[metric]
        valid = series.dropna()
        density = float(series.notna().mean())

        # density within the channel's own window keeps early-but-complete channels
        if not valid.empty:
            window = series.loc[valid.index.min() : valid.index.max()]
            window_density = float(window.notna().mean())
            window_span = len(window) / len(series)
        else:
            window_density = 0.0
            window_span = 0.0

        std = float(np.nanstd(series.to_numpy())) if not valid.empty else 0.0
        # warm-up spikes must not rescue a channel that is flat when cold
        cold_vals = series[cold].to_numpy()
        std_cold = float(np.nanstd(cold_vals)) if np.isfinite(cold_vals).any() else 0.0

        dense_enough = density >= min_density or (
            window_density >= WINDOW_MIN_DENSITY and window_span >= WINDOW_MIN_SPAN
        )
        # drop sparse or flat-when-cold channels
        flat_cold = not (std_cold > 0 and np.isfinite(std_cold))
        keep = dense_enough and not flat_cold
        if keep:
            kept[metric] = series
        reasons = [r for r, hit in [("sparse", not dense_enough), ("flat_cold", flat_cold)] if hit]

        catalog_rows.append(
            {
                "metric": metric,
                "n_replicas": len(cols),
                "zeros_to_nan": zeros_by_metric[metric],
                "density": round(density, 4),
                "window_density": round(window_density, 4),
                "window_span": round(window_span, 4),
                "std": std,
                "std_cold": std_cold,
                "first_valid": valid.index.min() if not valid.empty else pd.NaT,
                "last_valid": valid.index.max() if not valid.empty else pd.NaT,
                "kept": keep,
                "drop_reason": ", ".join(reasons) or None,
            }
        )

    wide = pd.DataFrame(kept).sort_index()
    wide.index.name = "timestamp"
    catalog = pd.DataFrame(catalog_rows).sort_values(
        ["kept", "metric"], ascending=[False, True], ignore_index=True
    )
    return wide, catalog


def cold_mask(
    wide: pd.DataFrame,
    mix_channel: str = MIX_CHANNEL,
    pt2_channel: str = PT2_CHANNEL,
    pt2_max: float = 10.0,
    mix_min: float = 0.0,
    mix_max: float = 0.05,
) -> pd.Series:
    """Return a boolean mask of rows where the fridge is cold.

    Needs mix_min < MIX < mix_max K; PT2 >= pt2_max K fails a row but a missing PT2
    doesn't (see datasheet 4, section 6).
    """
    mix = wide[mix_channel]
    pt2 = wide[pt2_channel]
    return ~(pt2 >= pt2_max) & (mix > mix_min) & (mix < mix_max)


def cold_epochs(wide: pd.DataFrame, max_gap: str = "24h", **cold_kwargs) -> pd.Series:
    """Number the cold epochs; a new one starts after a gap over max_gap between cold rows.

    Returns an Int64 series on wide's index, NA off the cold mask; cold_kwargs go to cold_mask.
    """
    t = wide.index[cold_mask(wide, **cold_kwargs).to_numpy()].to_series()
    epoch = (t.diff() > pd.Timedelta(max_gap)).cumsum()
    return epoch.reindex(wide.index).astype("Int64").rename("epoch")
