from typing import NamedTuple

import numpy as np
import pandas as pd

T = pd.Timestamp
Period = tuple[pd.Timestamp, pd.Timestamp]  # [start, end), naive UTC


class Break(NamedTuple):
    at: pd.Timestamp  # first time of the new regime
    what: str
    source: str  # 'ds1', 'ds2', 'ds3', 'ds4' or 'output_info'


class Untracked(NamedTuple):
    at: pd.Timestamp  # DS2 snapshot
    kind: str  # 'rollback' (restores an earlier snapshot) or 'unmatched'
    restores: pd.Timestamp | None  # the snapshot a rollback restores
    routines: tuple[str, ...]  # routines whose params it changes


class Flag(NamedTuple):
    period: Period  # first flagged calibrate_1q run, first run after
    qubits: tuple[int, ...] | None  # None = every qubit
    what: str


class Incident(NamedTuple):
    period: Period  # first bad run, first good run after the rollback; every source and parameter
    what: str


STUDY_PERIODS: dict[str, Period] = {
    "all": (T("2025-08-01"), T("2026-08-19")),
    "ds1_ds2_ds3": (T("2025-01-01"), T("2026-08-19")),
    "ds1_ds3": (T("2024-10-15"), T("2026-08-19")),
    "ds3": (T("2024-05-24"), T("2026-09-15")),
}

OUTAGES: tuple[Period, ...] = (
    (T("2025-02-05"), T("2025-02-12")),
    (T("2025-03-10"), T("2025-03-18")),
    (T("2026-03-12"), T("2026-03-24")),
)

DS4_GAPS: tuple[Period, ...] = (  # readings either side, not [start, end): mask DS4 rows with t > start
    (T("2026-03-17 00:00"), T("2026-03-17 18:00")),
    (T("2026-06-11 18:00"), T("2026-07-02 18:00")),
    (T("2026-07-07 06:00"), T("2026-07-08 18:00")),
)

THERMAL_CYCLES: tuple[Period, ...] = (  # DS4 warm-up: last cold reading before, first after (like DS4_GAPS)
    (T("2026-03-09 00:00"), T("2026-03-22 00:00")),
)

SETTLING: tuple[Period, ...] = (  # first cold DS4 reading after a thermal cycle, first settled day (B1)
    (T("2026-03-22 00:00"), T("2026-04-04 00:00")),
)

BREAKS: tuple[Break, ...] = (
    Break(T("2024-05-28"), "couplings 2-1 and 25-17 reverse", "ds3"),
    Break(T("2024-09-18"), "DS1 restore", "ds1"),
    Break(T("2025-02-12"), "outage ends (hand-written DS2 values)", "ds1"),
    Break(T("2025-02-17 16:11:02"), "calibrate_1q code change", "output_info"),
    Break(T("2025-03-18"), "outage ends (hand-written DS2 values)", "ds1"),
    Break(T("2025-03-20 12:00:58"), "calibrate_1q old code back", "output_info"),
    Break(T("2025-03-28 19:44:15"), "calibrate_1q new code back", "output_info"),
    Break(T("2025-07-07"), "DS3 gating era 2, error fields start", "ds3"),
    Break(T("2025-08-22"), "DS3 gating era 3", "ds3"),
    Break(T("2026-03-22 00:00"), "thermal cycle ends (f01 level shift)", "ds4"),
    Break(T("2026-03-24"), "couplings 1-10 and 1-2 reverse", "ds3"),
    Break(T("2026-05-21"), "DS3 openpulse_features added", "ds3"),
    Break(T("2026-05-25 22:01:25"), "DS2 records its last DC bias change", "ds2"),
)

_1Q, _2Q, _MX = "calibrate_1q", "calibrate_2q", "mixer_calibration"

UNTRACKED_DS2: tuple[Untracked, ...] = (  # snapshots with 50 or more changes no tracked run made
    Untracked(T("2025-02-12 13:40:14"), "unmatched", None, (_1Q,)),
    Untracked(T("2025-02-12 17:34:10"), "unmatched", None, (_1Q, _2Q)),
    Untracked(T("2025-02-23 08:00:14"), "unmatched", None, (_2Q,)),
    Untracked(T("2025-03-18 11:42:44"), "unmatched", None, (_MX,)),
    Untracked(T("2025-03-18 19:06:53"), "unmatched", None, (_1Q, _2Q)),
    Untracked(T("2025-05-12 09:15:09"), "unmatched", None, (_2Q,)),
    Untracked(T("2025-09-03 19:05:16"), "rollback", T("2025-09-02 20:13:31"), (_1Q, _2Q)),
    Untracked(T("2026-02-26 19:05:15"), "rollback", T("2026-02-25 21:09:14"), (_1Q, _2Q)),
    Untracked(T("2026-03-24 12:03:12"), "rollback", T("2026-03-05 20:11:50"), (_1Q, _2Q, _MX)),
    Untracked(T("2026-05-20 20:13:10"), "unmatched", None, (_MX,)),
    Untracked(T("2026-05-21 19:54:07"), "unmatched", None, (_MX,)),
    Untracked(T("2026-07-05 21:25:30"), "rollback", T("2026-07-01 20:04:47"), (_1Q, _2Q, _MX)),
)

UNTRACKED_DS2_OTHER: tuple[pd.Timestamp, ...] = (  # no-run snapshots under the UNTRACKED_DS2 bar (B2 section 1)
    T("2025-02-12 19:05:13"),
    T("2025-03-21 12:40:20"),
    T("2025-03-21 14:47:08"),
    T("2025-03-21 15:51:18"),
    T("2026-03-24 14:45:45"),
)

F01_FLAGS: tuple[Flag, ...] = (
    Flag((T("2025-03-24 19:44:14"), T("2025-04-25 12:13:42")), (23,), "Q23 residual over 50 MHz"),
    Flag((T("2026-03-09 19:42:45"), T("2026-03-24 14:45:52")), None, "chip-wide shift over 5 MHz"),
    Flag((T("2026-07-02 19:38:25"), T("2026-07-05 21:54:09")), None, "chip-wide shift over 5 MHz"),
)

INCIDENTS: tuple[Incident, ...] = (
    Incident((T("2026-03-09 08:45:16"), T("2026-03-24 14:45:52")), "thermal cycle"),
    Incident((T("2026-07-02 08:45:41"), T("2026-07-05 21:54:09")), "chip-wide f01 shift, fridge state unknown (DS4 gap)"),
)


def _naive(times) -> pd.DatetimeIndex:
    t = pd.DatetimeIndex(times)
    return t.tz_convert("UTC").tz_localize(None) if t.tz is not None else t


def within(times, *periods: Period) -> np.ndarray:
    """Return a mask: True where a time (naive UTC or tz-aware) falls in any [start, end) period."""
    t = _naive(times)
    mask = np.zeros(len(t), dtype=bool)
    for start, end in periods:
        mask |= (t >= start) & (t < end)
    return mask


def f01_flagged(times, qubits, flags=F01_FLAGS) -> np.ndarray:
    """Return a mask: True where a (time, qubit) pair falls in one of the f01 flags."""
    t = _naive(times)
    q = np.asarray(qubits)
    mask = np.zeros(len(t), dtype=bool)
    for f in flags:
        m = within(t, f.period)
        if f.qubits is not None:
            m &= np.isin(q, f.qubits)
        mask |= m
    return mask


def spans(t0, t1, at) -> np.ndarray:
    """Return a mask: True where some time in at falls in (t0, t1].

    Use it to drop DS2 differences between runs that span an untracked write.
    """
    a = np.sort(np.array(list(at), dtype="datetime64[ns]"))
    return np.searchsorted(a, _naive(t1).values, side="right") > np.searchsorted(a, _naive(t0).values, side="right")


def segment(times, breaks=BREAKS) -> np.ndarray:
    """Return the segment number per time: how many breaks fall at or before it.

    Pass a subset of BREAKS to split on those only.
    """
    at = np.sort(np.array([b.at for b in breaks], dtype="datetime64[ns]"))
    return np.searchsorted(at, _naive(times).values, side="right")
