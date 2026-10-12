import json
from pathlib import Path

import pandas as pd

from ...paths import CLEAN

CLEAN_DIR = CLEAN / "hardware_models"
INDEX_PATH = CLEAN_DIR / "hardware_model_index.parquet"
VALUES_PATH = CLEAN_DIR / "hardware_model_values.parquet"
PATHS_PATH = CLEAN_DIR / "hardware_model_paths.parquet"


def load_index(path: Path = INDEX_PATH) -> pd.DataFrame:
    """Load the snapshot index (one row per calibration snapshot, naive clock)."""
    return pd.read_parquet(path)


def load_paths(path: Path = PATHS_PATH) -> pd.DataFrame:
    """Load the path dictionary (one row per path_template)."""
    return pd.read_parquet(path)


def load_values(
    path: Path = VALUES_PATH,
    entity: str | None = None,
    path_template: str | None = None,
) -> pd.DataFrame:
    """Load the change-log values table (one row per contiguous unchanged run).

    Optionally filter by entity or path_template; valid_to_ts is NaT for a still-open run.
    """
    values = pd.read_parquet(path)
    if entity is not None:
        values = values[values["entity"] == entity]
    if path_template is not None:
        values = values[values["path_template"] == path_template]
    return values.reset_index(drop=True)


def decode_value(row: pd.Series):
    """Return a values-table row's native Python value (the inverse of clean.classify).

    enum -> member name, ref -> target path, json/empty -> parsed container, null -> None.
    """
    dtype = row["dtype"]
    if dtype == "null":
        return None
    if dtype == "int":
        return int(row["value_int"])
    if dtype == "float":
        return float(row["value_float"])
    if dtype == "bool":
        return bool(row["value_bool"])
    if dtype in ("str", "enum", "ref"):
        return row["value_str"]
    if dtype == "complex":
        re_, im_ = json.loads(row["value_json"])
        return complex(re_, im_)
    if dtype in ("json", "empty"):
        return json.loads(row["value_json"])
    raise ValueError(f"unknown dtype {dtype!r} on {row.get('path')!r}")


def numeric_values(values: pd.DataFrame) -> pd.Series:
    """Return values-table rows as complex numbers, vectorised; NaN for non-numeric dtypes."""
    out = values["value_float"].astype(float).fillna(values["value_int"].astype(float))
    cplx = values["dtype"] == "complex"
    parsed = values.loc[cplx, "value_json"].map(lambda s: complex(*json.loads(s)))
    out = out.astype(complex)
    out[cplx] = parsed
    return out


def values_at(at: pd.DataFrame, series: pd.DataFrame, by: list[str], col: str = "value") -> pd.Series:
    """Return series[col] in effect at each row of at (time t, keys by), aligned to at.

    series holds runs with valid_from_ts and valid_to_ts (NaT = still open). NaN where t is
    missing or no run covers it, e.g. the path was absent at that time.
    """
    left = at[["t", *by]].astype({"t": "datetime64[ns]"}).dropna(subset=["t"])
    left = left.rename_axis("_row").reset_index().sort_values("t")
    right = series[[*by, "valid_from_ts", "valid_to_ts", col]].astype(
        {"valid_from_ts": "datetime64[ns]", "valid_to_ts": "datetime64[ns]"})
    m = pd.merge_asof(left, right.sort_values("valid_from_ts"), left_on="t",
                      right_on="valid_from_ts", by=by)
    live = m["valid_to_ts"].isna() | (m["valid_to_ts"] >= m["t"])
    return pd.Series(m[col].where(live).to_numpy(), index=m["_row"]).reindex(at.index)


def value_as_of(
    values: pd.DataFrame, path: str, timestamp, index: pd.DataFrame | None = None
) -> object:
    """Return the value at path from the latest snapshot at or before timestamp.

    None means a stored null. Raises KeyError if no snapshot is that early or the path
    is absent from that snapshot, and ValueError if that snapshot failed to decode.
    Loads the snapshot index from disk unless you pass one.
    """
    snap = _snapshot_at(pd.Timestamp(timestamp), index)
    if snap is None:
        raise KeyError(f"no snapshot at or before {timestamp}")
    runs = _runs_covering(values[values["path"] == path], snap)
    if runs.empty:
        raise KeyError(f"{path!r} is absent from the snapshot at {snap}")
    return decode_value(runs.iloc[0])


def _require_decoded(row: pd.Series) -> None:
    """Raise ValueError if an index row is a snapshot that failed to decode."""
    if "loads_ok" in row.index and not row["loads_ok"]:
        raise ValueError(
            f"snapshot {row['sha256']} at {row['timestamp']} failed to decode, "
            "so its values are unknown"
        )


def _resolve_sha_timestamp(sha: str, index: pd.DataFrame | None) -> pd.Timestamp:
    """Map a snapshot sha256 (full or a unique prefix) to its timestamp.

    Raises KeyError if the sha is unknown or the prefix is ambiguous, and ValueError
    if that snapshot failed to decode.
    """
    if index is None:
        index = load_index()
    exact = index[index["sha256"] == sha]
    if len(exact) > 1:
        raise KeyError(f"sha {sha!r} is not unique in the index ({len(exact)} rows)")
    match = exact if len(exact) == 1 else index[index["sha256"].str.startswith(sha)]
    if len(match) > 1:
        raise KeyError(
            f"sha prefix {sha!r} matches {len(match)} snapshots; use more characters"
        )
    if match.empty:
        raise KeyError(f"sha {sha!r} not found in the snapshot index")
    row = match.iloc[0]
    _require_decoded(row)
    return pd.Timestamp(row["timestamp"])


def _runs_covering(values: pd.DataFrame, ts: pd.Timestamp) -> pd.DataFrame:
    """Return the runs whose validity interval contains ts; absent paths drop out."""
    open_from = values["valid_from_ts"] <= ts
    open_to = values["valid_to_ts"].isna() | (values["valid_to_ts"] >= ts)
    return values[open_from & open_to]


def _snapshot_at(ts: pd.Timestamp, index: pd.DataFrame | None) -> pd.Timestamp | None:
    """Return the time of the latest snapshot at or before ts, or None.

    Raises ValueError if that snapshot failed to decode.
    """
    if index is None:
        index = load_index()
    earlier = index[index["timestamp"] <= ts]
    if earlier.empty:
        return None
    row = earlier.loc[earlier["timestamp"].idxmax()]
    _require_decoded(row)
    return pd.Timestamp(row["timestamp"])


def snapshot_state(
    values: pd.DataFrame,
    *,
    sha: str | None = None,
    timestamp=None,
    index: pd.DataFrame | None = None,
    decode: bool = True,
) -> pd.DataFrame:
    """Return the whole device at one moment, one row per path.

    Pass sha (full or unique prefix) or timestamp (latest snapshot at or before it).
    decode=True adds a native value column next to dtype.
    """
    if (sha is None) == (timestamp is None):
        raise ValueError("pass exactly one of sha= or timestamp=")

    if sha is not None:
        ts = _resolve_sha_timestamp(sha, index)
    else:
        ts = _snapshot_at(pd.Timestamp(timestamp), index)
    live = values.iloc[:0] if ts is None else _runs_covering(values, ts)

    state = live.sort_values("path").reset_index(drop=True)
    if decode:
        decoded = (
            state.apply(decode_value, axis=1)
            if len(state)
            else pd.Series([], dtype="object")
        )
        state.insert(state.columns.get_loc("dtype") + 1, "value", decoded)
    return state


def decode_snapshot(gz_path: Path):
    """Decode a raw .gz snapshot into a live hardware model with maac's load_calibration.

    Raises ImportError outside .venv-maac, the only env with oqc-maac (plus qat and whisqrs).
    """
    try:
        from maac.measurement_environment.encoder import load_calibration
    except ImportError as exc:  # pragma: no cover - env-specific guard
        raise ImportError(
            "Could not import maac -- decoding a snapshot needs oqc-maac (with qat + whisqrs), "
            f"which live only in .venv-maac (underlying import error: {exc})"
        ) from exc
    return load_calibration(str(gz_path))
