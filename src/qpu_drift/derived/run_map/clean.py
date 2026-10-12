import numpy as np
import pandas as pd

from ...ids import coupling, coupling_key, qubit
from ...sources.hardware_models.load import numeric_values, values_at
from .constants import (
    CHANNEL_OWNER,
    COMPLEX_PARAMS,
    DS2_PATHS,
    EVENING_HOURS,
    HW_PARAMS,
    MORNING_HOURS,
    RTOL,
    STEPS_1Q,
    WARNINGS_1Q,
    WARNINGS_2Q,
)

_RUN_KEY = ["routine", "start"]


def run_window(start: pd.Timestamp) -> str:
    """Return the scheduled window a run starts in: morning, evening or other."""
    if start.hour in MORNING_HOURS:
        return "morning"
    if start.hour in EVENING_HOURS:
        return "evening"
    return "other"


def _ids(x) -> list[int]:
    """Return the int result ids in a result_ids leaf (an int or a list); drop anything else."""
    items = x if isinstance(x, list) else [x]
    return [int(i) for i in items if isinstance(i, int) and not isinstance(i, bool)]


def result_rows(oi: dict, routine: str) -> list[tuple[str, str | None, int]]:
    """Flatten a file's result_ids to (key, step, result_id) rows, keeping the raw key.

    Calibration nests key then step (2q may have no step); benchmarking nests step then key.
    """
    rows = []
    for outer, inner in (oi.get("result_ids") or {}).items():
        if not isinstance(inner, dict):
            rows += [(outer, None, i) for i in _ids(inner)]
            continue
        for k, leaf in inner.items():
            key, step = (k, outer) if routine == "benchmarking" else (outer, k)
            rows += [(key, step, i) for i in _ids(leaf)]
    return rows


def run_row(oi: dict, routine: str) -> dict:
    """Return per-run counts of result ids and warnings (NA if the file has no warnings key)."""
    warnings = oi.get("warnings")
    return {
        "n_result_ids": len(result_rows(oi, routine)),
        "n_warnings": len(warnings) if isinstance(warnings, list) else pd.NA,
    }


def warned(warnings: list | None, routine: str) -> set | None:
    """Return the entities a warning names: (qubit, param) pairs for 1q, couplings for 2q.

    None if the file has no warnings (mixer never writes them).
    """
    if not isinstance(warnings, list):
        return None
    out = set()
    for w in map(str, warnings):
        if routine == "calibrate_1q":
            out |= {(m.group(1), p) for rx, p in WARNINGS_1Q if (m := rx.search(w))}
        elif routine == "calibrate_2q":
            out |= {f"{m.group(1)}-{m.group(2)}" for rx in WARNINGS_2Q if (m := rx.search(w))}
    return out


def _num(x) -> complex | float | None:
    """Convert an hw_parameters value: complex dict -> complex, number -> float, else None."""
    if isinstance(x, dict) and "real" in x:
        return complex(x["real"], x.get("imag", 0.0))
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    return None


def _parts(z) -> tuple[float | None, float | None]:
    """Return the (real, imag) parts of a value, or (None, None) if it is missing."""
    if z is None:
        return None, None
    z = complex(z)
    return z.real, z.imag


def _entity(routine: str, key: str) -> tuple[str, int | None]:
    """Return the canonical entity string and owning qubit for an hw_parameters key."""
    if routine == "calibrate_1q":
        q = qubit(key)
        return str(q), q
    if routine == "calibrate_2q":
        pair = coupling(key)
        return coupling_key(pair), pair[0]
    return key, None


def _measured(oi: dict, routine: str, key: str, param: str, owner: int | None):
    """True if the run measured this entity for this param, judged from its result_ids.

    1q: an id at a step in STEPS_1Q (Rabi left out, see B2 section 2). 2q: the coupling has an id,
    which the producer writes only on accept. Mixer: one mixer_cal id covers the owner's drive and
    measure channels together, so NA when only one of the two is present.
    """
    rids = oi.get("result_ids") or {}
    if routine == "calibrate_1q":
        steps = rids.get(key) or {}
        return any(_ids(steps.get(s, [])) for s in STEPS_1Q[param])
    if routine == "calibrate_2q":
        return any(coupling(k) == coupling(key) for k in rids)
    if owner is None:
        return pd.NA
    n = len(_ids((rids.get(f"Q{owner}") or {}).get("mixer_cal", [])))
    return {0: False, 2: True}.get(n, pd.NA)


def differs(a, b):
    """Return nullable elementwise 'a differs from b' (rtol RTOL); NA where either is missing."""
    a = pd.to_numeric(pd.Series(a), errors="coerce").to_numpy(float)
    b = pd.to_numeric(pd.Series(b), errors="coerce").to_numpy(float)
    out = pd.array(~np.isclose(a, b, rtol=RTOL, atol=0.0), dtype="boolean")
    out[np.isnan(a) | np.isnan(b)] = pd.NA
    return out


def entity_rows(oi: dict, routine: str, channel_qubits: dict[str, int] | None = None) -> pd.DataFrame:
    """Build one row per entity and param of a calibration file.

    Columns are old, new, measured, warned and changed. Complex params split into _re and _im
    rows; channel_qubits maps mixer channels to qubits.
    """
    hw = oi.get("hw_parameters") or {}
    warn = warned(oi.get("warnings"), routine)
    rows = []
    for hw_key, param in HW_PARAMS.get(routine, {}).items():
        old, new = hw.get(f"old_{hw_key}") or {}, hw.get(f"new_{hw_key}") or {}
        for key in dict.fromkeys([*old, *new]):
            entity, owner = _entity(routine, key)
            if routine == "mixer_calibration":
                owner = (channel_qubits or {}).get(key)
            if warn is None:
                was_warned = pd.NA
            elif routine == "calibrate_1q":
                was_warned = (entity, param) in warn
            else:
                was_warned = entity in warn
            base = {
                "entity": entity,
                "qubit": owner,
                "measured": _measured(oi, routine, key, param, owner),
                "warned": was_warned,
            }
            o, n = _num(old.get(key)), _num(new.get(key))
            if param in COMPLEX_PARAMS:
                (o_re, o_im), (n_re, n_im) = _parts(o), _parts(n)
                rows.append({**base, "param": f"{param}_re", "old": o_re, "new": n_re})
                rows.append({**base, "param": f"{param}_im", "old": o_im, "new": n_im})
            else:
                rows.append({**base, "param": param, "old": o, "new": n})
    df = pd.DataFrame(rows, columns=["entity", "qubit", "param", "old", "new", "measured", "warned"])
    df["qubit"] = df["qubit"].astype("Int64")
    df[["old", "new"]] = df[["old", "new"]].astype(float)
    df[["measured", "warned"]] = df[["measured", "warned"]].astype("boolean")
    df["changed"] = differs(df["old"], df["new"])
    return df


def snapshots_around(starts: pd.Series, snapshots: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return the last DS2 snapshot at or before each start and the first strictly after it.

    NaT where a side has none; after is also NaT when before is (see datasheet 2).
    """
    snaps = np.sort(pd.to_datetime(snapshots).to_numpy("datetime64[ns]"))
    s = pd.to_datetime(starts).to_numpy("datetime64[ns]")
    i = np.searchsorted(snaps, s, side="right")
    nat = np.datetime64("NaT", "ns")
    if len(snaps) == 0:
        empty = pd.Series(np.full(len(s), nat), index=starts.index)
        return empty, empty.copy()
    before = np.where(i > 0, snaps[np.clip(i - 1, 0, None)], nat)
    after = np.where((i > 0) & (i < len(snaps)), snaps[np.clip(i, None, len(snaps) - 1)], nat)
    return pd.Series(before, index=starts.index), pd.Series(after, index=starts.index)


def ds2_series(values: pd.DataFrame) -> pd.DataFrame:
    """Pull the tracked params from the DS2 change-log, keyed like entity_rows.

    Complex params are split into _re and _im rows with a float value.
    """
    parts = []
    for rx, routine, param in DS2_PATHS:
        m = values["path"].str.fullmatch(rx.pattern)
        if not m.any():
            continue
        sub = values[m]
        g = sub["path"].str.extract(rx)
        if "q" in g:
            entity = g["q"]
        elif "c" in g:
            entity = g["c"] + "-" + g["t"]
        else:
            entity = g["ch"]
        z = numeric_values(sub)
        base = pd.DataFrame({
            "routine": routine,
            "entity": entity.to_numpy(),
            "valid_from_ts": sub["valid_from_ts"].to_numpy("datetime64[ns]"),
            "valid_to_ts": sub["valid_to_ts"].to_numpy("datetime64[ns]"),
        })
        if param in COMPLEX_PARAMS:
            parts.append(base.assign(param=f"{param}_re", value=z.to_numpy().real))
            parts.append(base.assign(param=f"{param}_im", value=z.to_numpy().imag))
        else:
            parts.append(base.assign(param=param, value=z.to_numpy().real))
    cols = ["routine", "param", "entity", "valid_from_ts", "valid_to_ts", "value"]
    return pd.concat(parts, ignore_index=True)[cols] if parts else pd.DataFrame(columns=cols)


def channel_qubits(values: pd.DataFrame) -> dict[str, int]:
    """Map each physical channel (Ch1) to its owning qubit, from DS2 pulse-channel ids."""
    ids = values.loc[values["entity"] == "pulse_channels", "entity_id"].dropna().unique()
    out: dict[str, int] = {}
    for m in filter(None, (CHANNEL_OWNER.fullmatch(i) for i in ids)):
        q = int(m.group("q"))
        if out.setdefault(m.group("ch"), q) != q:
            raise ValueError(f"{m.group('ch')} maps to qubits {out[m.group('ch')]} and {q}")
    return out


def attach_ds2(entities: pd.DataFrame, runs: pd.DataFrame, series: pd.DataFrame) -> pd.DataFrame:
    """Add the DS2 value before and after each run, and whether it changed (written).

    A side is missing where the path was absent at that snapshot; written is NA unless both exist.
    """
    snaps = runs[[*_RUN_KEY, "snap_before", "snap_after"]]
    req = entities[[*_RUN_KEY, "param", "entity"]].merge(snaps, on=_RUN_KEY, how="left")
    req.index = entities.index
    out = entities.copy()
    for side in ("before", "after"):
        at = req.assign(t=req[f"snap_{side}"])
        out[side] = values_at(at, series, ["routine", "param", "entity"])
    out["written"] = differs(out["before"], out["after"])
    return out


def ds3_link(kpi: pd.DataFrame) -> pd.Series:
    """Map each benchmarking start (DS3 measured_at) to its DS3 run (created_at)."""
    k = kpi.dropna(subset=["measured_at"])
    link = k.groupby(k["measured_at"].astype("datetime64[ns]"))["created_at"].min()
    return link.astype("datetime64[ns]")


def assign_rows(results: pd.DataFrame, spine: pd.DataFrame) -> pd.DataFrame:
    """Map DS1 rows to the run they ran in: one row per id with routine, start and claimed.

    A claimed row takes its claiming run. Any other row takes the run whose span contains its
    time_start. A span runs from the earlier of the run start and its first claimed row to the end
    of its last claimed row. Rows outside every span (ad-hoc work) are left out.
    """
    rows = spine[["id", "time_start", "time_end"]].astype(
        {"time_start": "datetime64[ns]", "time_end": "datetime64[ns]"})
    valid = results.loc[results["valid"], [*_RUN_KEY, "result_id"]].drop_duplicates("result_id")
    claimed = valid.merge(rows, left_on="result_id", right_on="id")
    span = claimed.groupby(_RUN_KEY).agg(first=("time_start", "min"), last=("time_end", "max"))
    span = span.reset_index()
    span["from"] = span[["start", "first"]].min(axis=1)
    rest = rows[~rows["id"].isin(claimed["id"])].sort_values("time_start")
    m = pd.merge_asof(rest, span.sort_values("from")[[*_RUN_KEY, "from", "last"]],
                      left_on="time_start", right_on="from")
    m = m[m["time_start"] <= m["last"]]
    out = pd.concat([claimed[["id", *_RUN_KEY]].assign(claimed=True),
                     m[["id", *_RUN_KEY]].assign(claimed=False)], ignore_index=True)
    out["start"] = out["start"].astype("datetime64[ns]")
    return out.sort_values("id", ignore_index=True)
