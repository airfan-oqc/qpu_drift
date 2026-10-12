import pandas as pd

from qpu_drift.periods import (
    BREAKS, F01_FLAGS, INCIDENTS, OUTAGES, SETTLING, STUDY_PERIODS, THERMAL_CYCLES, UNTRACKED_DS2, UNTRACKED_DS2_OTHER,
    Break, f01_flagged, segment, spans, within,
)


def test_within_is_end_exclusive():
    start, end = STUDY_PERIODS["all"]
    t = [start - pd.Timedelta("1s"), start, end - pd.Timedelta("1s"), end]
    assert within(t, (start, end)).tolist() == [False, True, True, False]


def test_within_any_outage():
    t = ["2025-02-04 19:45", "2025-02-08", "2025-03-17 23:00", "2025-03-18 08:00"]
    assert within(t, *OUTAGES).tolist() == [False, True, True, False]


def test_within_accepts_tz_aware_times():
    t = pd.DatetimeIndex(["2026-06-20 12:00"]).tz_localize("UTC")
    assert within(t, (pd.Timestamp("2026-06-11"), pd.Timestamp("2026-07-02"))).tolist() == [True]


def test_segment_starts_new_segment_at_break():
    b = [Break(pd.Timestamp("2025-02-17 16:11:02"), "code change", "output_info")]
    t = ["2025-02-17 14:18:25", "2025-02-17 16:11:02", "2025-02-17 19:45:26"]
    assert segment(t, b).tolist() == [0, 1, 1]


def test_breaks_are_in_time_order():
    at = [b.at for b in BREAKS]
    assert at == sorted(at)


def test_untracked_are_in_time_order_and_only_rollbacks_restore():
    assert [u.at for u in UNTRACKED_DS2] == sorted(u.at for u in UNTRACKED_DS2)
    assert all((u.kind == "rollback") == (u.restores is not None) for u in UNTRACKED_DS2)


def test_untracked_other_is_sorted_and_disjoint_from_untracked():
    assert list(UNTRACKED_DS2_OTHER) == sorted(UNTRACKED_DS2_OTHER)
    assert not set(UNTRACKED_DS2_OTHER) & {u.at for u in UNTRACKED_DS2}


def test_spans_is_left_open_right_closed():
    at = [pd.Timestamp("2026-07-05 21:25:30")]
    t0 = ["2026-07-05 19:34:00", "2026-07-05 21:25:30", "2026-07-05 08:45"]
    t1 = ["2026-07-05 21:54:09", "2026-07-06 08:45", "2026-07-05 21:25:30"]
    assert spans(t0, t1, at).tolist() == [True, False, True]


def test_f01_flagged_by_qubit_and_chip_wide():
    t = ["2025-04-01", "2025-04-01", "2025-04-25 12:13:42", "2026-07-03"]
    assert f01_flagged(t, [23, 1, 23, 1]).tolist() == [True, False, False, True]


def test_chip_wide_f01_flags_lie_inside_an_incident():
    for f in F01_FLAGS:
        if f.qubits is None:
            assert any(a <= f.period[0] and f.period[1] <= b for (a, b), _ in INCIDENTS)


def test_settling_starts_where_a_thermal_cycle_ends():
    assert [s for s, _ in SETTLING] == [e for _, e in THERMAL_CYCLES]
    assert all(s < e for s, e in SETTLING)


def test_every_thermal_cycle_end_is_a_break():
    assert {e for _, e in THERMAL_CYCLES} <= {b.at for b in BREAKS}


def test_every_outage_without_a_thermal_cycle_ends_in_a_break():
    for o0, o1 in OUTAGES:
        if not any(s < o1 and o0 < e for s, e in THERMAL_CYCLES):
            assert o1 in {b.at for b in BREAKS}


def test_thermal_cycles_are_ordered_and_overlap_a_2026_outage():
    for start, end in THERMAL_CYCLES:
        assert start < end
        assert any(start < o1 and o0 < end for o0, o1 in OUTAGES)
