import re

import numpy as np
import pandas as pd

from ...ids import coupling

QUBIT_RE = re.compile(r"Q(\d+)")            # &Q1&&Q4& tags; Q skips readout labels like &00&
COUPLING_RE = re.compile(r"C(\d+)-T(\d+)")  # &C4-T5& on two-qubit rows (directed)

TIER = {
    "MeasureBands": "mixer_hygiene",  # ~64% of measurements
    "LOLeakageOptimisation": "mixer_hygiene",
    "SidebandImbalanceOptimisation": "mixer_hygiene",
    "SidebandPhaseOptimisation": "mixer_hygiene",
    "MixerCalibration": "mixer_hygiene",
    "CRUnitaryIZ": "two_qubit",  # ~27%
    "CRUnitaryTomography": "two_qubit",
    "CrossResonanceRabi": "two_qubit",
    "RandomizedBenchmarking2q": "two_qubit",
    "InterleavedBenchmarking2q": "two_qubit",
    "CrLeakage": "two_qubit",
    "CRDrag": "two_qubit",
    "Ramsey": "one_qubit_physics",  # ~8%, highest signal
    "Rabi": "one_qubit_physics",
    "FineFrequency": "one_qubit_physics",
    "FineFreqShift": "one_qubit_physics",
    "XPi2Amp": "one_qubit_physics",
    "XPi2Drag": "one_qubit_physics",
    "T1": "one_qubit_physics",
    "T2echo": "one_qubit_physics",
    "RandomizedBenchmarking1q": "one_qubit_physics",
    "Ramsey12": "one_qubit_physics",
    "Rabi12": "one_qubit_physics",
    "XPi2Amp12": "one_qubit_physics",
    "RamseyJ": "one_qubit_physics",
    "ZMap": "readout",  # ~1.7%
    "ReadoutMatrix": "readout",
    "ReadoutAmp": "readout",
    "ReadoutWidth": "readout",
    "ReadoutFrequency": "readout",
    "ZMapDDrop": "readout",
    "ResonatorSpectroscopy": "spectroscopy",  # <1%
    "FastResonatorSpectroscopy": "spectroscopy",
    "FastResonatorPeakFinder": "spectroscopy",
    "FastQubitPeakFinder": "spectroscopy",
    "QubitPeakFinder": "spectroscopy",
    "ResonatorChiMeasurement": "spectroscopy",
    "DDropTemp": "diagnostics",  # <1%; own bucket so "other" stays empty
    "DDropTotal": "diagnostics",
    "FineFrequencyTemp": "diagnostics",
    "RabiPopulation": "diagnostics",
    "RabiQubitTemperature": "diagnostics",
}

DEFAULT_EPOCH_GAP_H = 24.0
ID_TIME_TOLERANCE = pd.Timedelta(hours=24)  # either side of the run start (datasheet 1)


def parse_qubits(data_keys: str) -> list[int]:
    """Parse &Q1&&Q4& -> [1, 4], deduped and sorted; non-qubit tags give [].

    Also recovers the one malformed &QQ14& tag as qubit 14.
    """
    return sorted({int(n) for n in QUBIT_RE.findall(data_keys)})


def parse_couplings(data_keys: str) -> list[str]:
    """Parse &C4-T5& -> ['C4-T5'], deduped and sorted; direction is kept."""
    return sorted({f"C{c}-T{t}" for c, t in COUPLING_RE.findall(data_keys)})


def target_type(n_qubits: int, n_couplings: int) -> str:
    """Classify a measurement's targets from its parsed entity counts."""
    # qubit and coupling tags never co-occur, but keep "mixed" so this is total
    if n_qubits and n_couplings:
        return "mixed"
    if n_qubits:
        return "qubit"
    if n_couplings:
        return "coupling"
    return "none"  # readout joint-state labels (&00&&01&) or empty data_keys


def build_spine(df: pd.DataFrame) -> pd.DataFrame:
    """Turn the raw result_table into a tidy per-measurement spine.

    One row in, one row out; timestamps stay naive (the server clock is UTC).
    """
    out = df.copy()
    out["time_start"] = pd.to_datetime(out["time_start"], errors="coerce")
    out["time_end"] = pd.to_datetime(out["time_end"], errors="coerce")
    out["duration_s"] = (out["time_end"] - out["time_start"]).dt.total_seconds()

    out["qubits"] = out["data_keys"].map(parse_qubits)
    out["couplings"] = out["data_keys"].map(parse_couplings)
    out["n_qubits"] = out["qubits"].str.len()
    out["n_couplings"] = out["couplings"].str.len()
    out["target_type"] = [
        target_type(nq, nc) for nq, nc in zip(out["n_qubits"], out["n_couplings"])
    ]

    # all 42 types are mapped; "other" only catches types added after TIER
    out["tier"] = out["measurement_type"].map(TIER).fillna("other")
    out["date"] = out["time_start"].dt.date
    out["month"] = out["time_start"].dt.to_period("M").astype(str)
    out["week"] = out["time_start"].dt.to_period("W").astype(str)
    return out.sort_values("time_start").reset_index(drop=True)


def validate_spine(spine: pd.DataFrame) -> dict[str, int]:
    """Return build-time sanity counts; every value should be 0."""
    return {
        "nat_time_start": int(spine["time_start"].isna().sum()),
        "nat_time_end": int(spine["time_end"].isna().sum()),
        "negative_duration": int((spine["duration_s"] < 0).sum()),
        "duplicate_id": int(spine["id"].duplicated().sum()),
        "mixed_target_type": int((spine["target_type"] == "mixed").sum()),
        "other_tier": int((spine["tier"] == "other").sum()),
    }


def valid_ids(result_id, expected_time, spine: pd.DataFrame, expected_type=None,
              tolerance: pd.Timedelta = ID_TIME_TOLERANCE) -> np.ndarray:
    """True where an id's spine row starts within tolerance of expected_time.

    With expected_type (one per id), the row must also have that measurement_type.
    Ids missing from the spine never pass; reused ids fail on time (see datasheet 1).
    """
    rows = spine.set_index("id")
    ids = pd.Series(result_id).reset_index(drop=True)
    expected = pd.to_datetime(pd.Series(expected_time).reset_index(drop=True))
    gap = (pd.to_datetime(ids.map(rows["time_start"])) - expected).abs()
    ok = (gap <= tolerance).to_numpy()
    if expected_type is not None:
        want = pd.Series(expected_type).reset_index(drop=True)
        ok = ok & (ids.map(rows["measurement_type"]) == want).to_numpy()
    return ok


_LONG_COLS = [
    "id", "measurement_name", "measurement_type", "tier",
    "time_start", "time_end", "duration_s", "relative_filepath",
    "date", "month", "week",
]


def explode_qubits(spine: pd.DataFrame) -> pd.DataFrame:
    """Explode the spine to one row per (measurement, qubit)."""
    ex = spine[_LONG_COLS + ["qubits"]].explode("qubits")
    ex = ex.dropna(subset=["qubits"]).rename(columns={"qubits": "qubit"})
    ex["qubit"] = ex["qubit"].astype(int)
    return ex.reset_index(drop=True)


def explode_couplings(spine: pd.DataFrame) -> pd.DataFrame:
    """Explode the spine to one row per (measurement, coupling) with control/target ints."""
    ex = spine[_LONG_COLS + ["couplings"]].explode("couplings")
    ex = ex.dropna(subset=["couplings"]).rename(columns={"couplings": "coupling"})
    pairs = ex["coupling"].map(coupling)
    ex["control"] = pairs.map(lambda p: p[0]).astype(int)
    ex["target"] = pairs.map(lambda p: p[1]).astype(int)
    return ex.reset_index(drop=True)


def coverage(long_df: pd.DataFrame, entity_col: str) -> pd.DataFrame:
    """Return an entity x measurement_type event-count matrix with a row total."""
    m = (
        long_df.pivot_table(
            index=entity_col, columns="measurement_type",
            values="id", aggfunc="count", fill_value=0,
        )
        .astype(int)
        .sort_index()
    )
    m["_TOTAL"] = m.sum(axis=1)
    return m


def cadence(long_df: pd.DataFrame, entity_col: str) -> pd.DataFrame:
    """Return count, span and gap stats per (entity, measurement_type)."""
    g = long_df.sort_values("time_start").groupby([entity_col, "measurement_type"])
    gaps_h = (
        g["time_start"].diff().dt.total_seconds().div(3600.0)
    )
    long_df = long_df.assign(_gap_h=gaps_h)
    rows = []
    for (ent, mt), grp in long_df.groupby([entity_col, "measurement_type"]):
        ts = grp["time_start"].dropna()
        gh = grp["_gap_h"].dropna()
        rows.append({
            entity_col: ent,
            "measurement_type": mt,
            "tier": grp["tier"].iloc[0],
            "n": len(grp),
            "first": ts.min(),
            "last": ts.max(),
            "span_days": (ts.max() - ts.min()).total_seconds() / 86400.0
            if len(ts) > 1 else 0.0,
            "median_gap_h": float(gh.median()) if len(gh) else pd.NA,
            "p90_gap_h": float(gh.quantile(0.90)) if len(gh) else pd.NA,
            "max_gap_h": float(gh.max()) if len(gh) else pd.NA,
        })
    return (
        pd.DataFrame(rows)
        .sort_values(["measurement_type", entity_col])
        .reset_index(drop=True)
    )


def device_epochs(spine: pd.DataFrame, gap_hours: float = DEFAULT_EPOCH_GAP_H) -> pd.DataFrame:
    """Return global inactivity gaps over gap_hours (candidate downtime or epoch breaks).

    Mixer hygiene runs many times a day, so a 24 h gap means the whole device paused.
    """
    ts = spine["time_start"].dropna().sort_values().reset_index(drop=True)
    gaps = ts.diff()
    mask = gaps > pd.Timedelta(hours=gap_hours)
    out = pd.DataFrame({
        "gap_start": ts.shift(1)[mask],
        "gap_end": ts[mask],
        "gap_hours": gaps[mask].dt.total_seconds() / 3600.0,
    })
    return out.dropna(subset=["gap_start"]).sort_values("gap_start").reset_index(drop=True)


def monthly_mix(spine: pd.DataFrame) -> pd.DataFrame:
    """Return a month x tier event-count matrix."""
    return (
        spine.pivot_table(
            index="month", columns="tier", values="id",
            aggfunc="count", fill_value=0,
        )
        .astype(int)
        .sort_index()
    )
