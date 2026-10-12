import pandas as pd

from .constants import (
    FILTER,
    GATE,
    KPI_COLS_L1,
    KPI_COLS_L2,
    METRIC_2Q,
    METRICS_1Q,
    PHYS_RANGE,
    R2_CRASH,
    R2_CRASH_METRICS,
    R2_GATE,
    RUN_COLS_L1,
    RUN_COLS_L2,
    RUN_MATCH_TOLERANCE,
    UNGATED,
    VALUE_SOURCES,
    VALUE_STATUSES,
)


def gating_regime(created_at) -> str:
    """Map a run's created_at to its producer gating era (see datasheet 3)."""
    ts = pd.Timestamp(created_at)
    if ts < FILTER:
        return "era0_ungated"
    if ts < UNGATED:
        return "era1_filter"
    if ts < GATE:
        return "era2_ungated"
    return "era3_postproc"


def r2_pregated(created_at, metric) -> bool:
    """True if a served value with no R2 was still R2-gated by the producer (see datasheet 3)."""
    ts = pd.Timestamp(created_at)
    if gating_regime(ts) != "era1_filter":
        return False
    return not (metric in R2_CRASH_METRICS and ts >= R2_CRASH)


def _present(x) -> bool:
    """True if x is a real value, not None, NaN, NA or NaT."""
    return not pd.isna(x)


def gate(metric, value, r2, pregated: bool = False) -> str | None:
    """Apply the physical acceptance rule shared by L1 and L2 (see datasheet 3).

    Returns ok, out_of_range, low_r2, no_fit, no_r2 or no_result, or None if the cell can't
    be decided.
    """
    if metric != "fRO" and _present(value) and value == 0:
        # 0 is a placeholder, not a fit result: with an R2 (0) it marks a failed fCX fit,
        # without one it's era 0's pre-filled 0.0 for a coupling whose RB2q never ran (DS3 section 6)
        return "no_fit" if _present(r2) else "no_result"
    if metric == "fRO":
        if not _present(value) or value == 0:
            return None
        lo, hi = PHYS_RANGE["fRO"]
        return "ok" if lo < value <= hi else "out_of_range"
    if _present(r2):
        if r2 == 0:
            return "no_fit"                 # no-fit sentinel
        if r2 < R2_GATE:
            return "low_r2"                 # R2 gate, ahead of the range check
        lo, hi = PHYS_RANGE[metric]
        return "ok" if _present(value) and lo < value <= hi else "out_of_range"
    if _present(value):
        lo, hi = PHYS_RANGE[metric]
        if not lo < value <= hi:
            return "out_of_range"
        return "ok" if pregated else "no_r2"
    return None


def classify(metric, value, r2, warned: bool = False,
             in_output_info: bool = False, has_result_id: bool = False,
             pregated: bool = False) -> str:
    """Return the L2 value_status: the shared gate, then settle nulls from output_info."""
    status = gate(metric, value, r2, pregated=pregated)
    if status is not None:
        return status
    if not in_output_info:
        return "unresolved"                 # run absent from the cache
    return "no_fit" if warned and has_result_id and metric != "fRO" else "no_result"


def flatten_payload(payload: dict) -> tuple[dict, list[dict]]:
    """Split one benchmarking payload into its run row and its ungated KPI rows."""
    general = payload["general"]
    created_at = general.get("created_at")
    run = {
        "created_at": created_at,
        "topology": general.get("topology"),
        "has_openpulse": "openpulse_features" in payload,
    }

    rows: list[dict] = []
    for entry in payload.get("one_qubit", {}).values():
        element = str(entry.get("qubit"))
        for metric in METRICS_1Q:
            rows.append(_kpi_row(created_at, element, "qubit", metric,
                                 entry.get(metric),
                                 entry.get(f"{metric}_error"),
                                 entry.get(f"{metric}_r2")))
    for element, entry in payload.get("two_qubit", {}).items():
        rows.append(_kpi_row(created_at, element, "coupling", METRIC_2Q,
                             entry.get("fCX"),
                             entry.get("fCX_error"),
                             entry.get("fCX_r2")))
    return run, rows


def _kpi_row(created_at, element, element_type, metric, value, error, r2) -> dict:
    """Build one ungated KPI row with the served value, error and r2 as-is."""
    return {
        "created_at": created_at,
        "element": element,
        "element_type": element_type,
        "metric": metric,
        "value": value,
        "error": error,
        "r2": r2,
    }


def assemble(payloads: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Flatten cached payloads into the ungated (runs, kpi) spine shared by L1 and L2.

    Malformed payloads are skipped; created_at is naive UTC.
    """
    runs: list[dict] = []
    kpi: list[dict] = []
    for payload in payloads:
        if not {"general", "one_qubit", "two_qubit"} <= payload.keys():
            continue
        if not payload["general"].get("created_at"):
            continue
        run, rows = flatten_payload(payload)
        runs.append(run)
        kpi.extend(rows)

    runs_df = (
        pd.DataFrame(runs, columns=RUN_COLS_L1)
        .drop_duplicates("created_at")
        .sort_values("created_at")
        .reset_index(drop=True)
    )
    runs_df["created_at"] = pd.to_datetime(runs_df["created_at"])

    kpi_df = pd.DataFrame(kpi, columns=KPI_COLS_L1)
    kpi_df["created_at"] = pd.to_datetime(kpi_df["created_at"])
    kpi_df = kpi_df.sort_values(["created_at", "metric", "element"]).reset_index(drop=True)
    return runs_df, kpi_df


def gate_good_values(kpi: pd.DataFrame) -> pd.DataFrame:
    """Null every value the shared gate doesn't pass (L1); r2 and error are kept."""
    out = kpi.copy()
    passed = pd.Series(
        [gate(m, v, r, pregated=r2_pregated(t, m)) == "ok"
         for t, m, v, r in zip(out["created_at"], out["metric"], out["value"], out["r2"])],
        index=out.index,
    )
    out["value"] = out["value"].where(passed)
    return out[KPI_COLS_L1]


def add_gating_regime(runs: pd.DataFrame) -> pd.DataFrame:
    """Stamp each run with its producer gating era (L2 only)."""
    out = runs.copy()
    out["created_at"] = pd.to_datetime(out["created_at"])
    out["gating_regime"] = out["created_at"].map(gating_regime)
    return out[RUN_COLS_L2]


def _asof_join_provenance(out: pd.DataFrame, prov: pd.DataFrame) -> pd.DataFrame:
    """Join output_info provenance onto the spine: backward as-of per run, then exact per cell.

    Unmatched runs keep oi_run null and stay unresolved (see datasheet 3).
    """
    prov = prov.copy()
    prov["measured_at"] = pd.to_datetime(prov["measured_at"])
    prov = prov.sort_values("measured_at").drop_duplicates(
        ["measured_at", "metric", "element"], keep="last")
    out["created_at"] = pd.to_datetime(out["created_at"])

    served_runs = out[["created_at"]].drop_duplicates().sort_values("created_at")
    oi_runs = pd.DataFrame({"oi_run": sorted(prov["measured_at"].unique())})
    run_map = pd.merge_asof(
        served_runs, oi_runs, left_on="created_at", right_on="oi_run",
        direction="backward", tolerance=RUN_MATCH_TOLERANCE,
    )
    out = out.merge(run_map, on="created_at", how="left")

    keyed = prov.dropna(subset=["metric"]).rename(columns={
        "measured_at": "measured_at_oi", "value": "value_oi", "r2": "r2_oi",
        "result_id": "result_id_oi"})
    keyed["oi_run"] = keyed["measured_at_oi"]
    return out.merge(
        keyed[["oi_run", "metric", "element", "value_oi", "r2_oi", "warned",
               "result_id_oi", "measured_at_oi"]],
        on=["oi_run", "metric", "element"], how="left",
    )


def _derive_value_source(merged: pd.DataFrame, resolved_value: pd.Series,
                         resolved_r2: pd.Series, in_oi: pd.Series) -> pd.Series:
    """Return which store supplied each value: qcaas, output_info, ds1 or none.

    ds1 marks a null low_r2/out_of_range reject with a real R2 on a run in output_info.
    """
    value_source = pd.Series("none", index=merged.index)
    value_source = value_source.mask(merged["value_oi"].notna(), "output_info")
    value_source = value_source.mask(merged["value"].notna(), "qcaas")
    ds1_fill = (
        resolved_value.isna()
        & merged["value_status"].isin(["low_r2", "out_of_range"])
        & resolved_r2.notna() & (resolved_r2 != 0)
        & in_oi
    )
    return value_source.mask(ds1_fill, "ds1")


def enrich_kpi(kpi: pd.DataFrame, prov: pd.DataFrame) -> pd.DataFrame:
    """Add value_status, value_source and DS1 pointers to the ungated spine (L2).

    Values coalesce qcaas -> output_info; ds1 cells stay NaN until decode-ds1 (see datasheet 3).
    Raises if prov is empty.
    """
    if prov is None or prov.empty:
        raise ValueError(
            "enrich_kpi requires a non-empty output_info provenance cache; "
            "Level 2 is enrichment. With no cache, use the Level-1 baseline "
            "(`build-benchmarking` / gate_good_values), not a cache-less L2."
        )
    out = kpi.copy()
    out["result_id"] = pd.NA
    out["measured_at"] = pd.NaT

    merged = _asof_join_provenance(out, prov)

    # only runs in output_info carry a DS1 pointer
    in_oi = merged["oi_run"].notna()
    warned = merged["warned"].fillna(False).astype(bool)
    # on a matched run R2 came from output_info, so a still-missing one was never checked
    pregated = pd.Series(
        [r2_pregated(t, m) for t, m in zip(merged["created_at"], merged["metric"])],
        index=merged.index) & ~in_oi

    merged["result_id"] = merged["result_id_oi"].where(
        merged["result_id_oi"].notna(), merged["result_id"])
    # keep ids as nullable ints; the as-of merge NaNs would upcast to float
    merged["result_id"] = merged["result_id"].astype("Int64")
    merged["measured_at"] = merged["measured_at_oi"].where(
        merged["measured_at_oi"].notna(), merged["measured_at"])
    has_rid = merged["result_id"].notna()   # per-element pointer (fRB/fCX)

    # resolve R2 and value served -> output_info; ds1 is only marked, value stays NaN
    resolved_r2 = merged["r2"].where(merged["r2"].notna(), merged["r2_oi"])
    resolved_value = merged["value"].where(merged["value"].notna(), merged["value_oi"])

    # reclassify every cell; only output_info-recovered fRO nulls move (to ok)
    merged["value_status"] = [
        classify(metric, value, r2, warned=w, in_output_info=io, has_result_id=hr,
                 pregated=pg)
        for metric, value, r2, w, io, hr, pg in zip(
            merged["metric"], resolved_value, resolved_r2, warned, in_oi, has_rid,
            pregated)
    ]

    merged["value_source"] = _derive_value_source(
        merged, resolved_value, resolved_r2, in_oi)

    merged["value"] = resolved_value   # NaN if absent, never 0
    merged["r2"] = resolved_r2

    return merged.drop(
        columns=["oi_run", "value_oi", "r2_oi", "warned",
                 "result_id_oi", "measured_at_oi"]
    )[KPI_COLS_L2]


def validate_l1(runs: pd.DataFrame, kpi: pd.DataFrame) -> dict[str, int]:
    """Return L1 sanity counts; every key but n_good should be 0."""
    # Re-states the gate rule on purpose as a drift tripwire; don't route it through gate().
    present = kpi["value"].notna()
    lo = kpi["metric"].map(lambda m: PHYS_RANGE.get(m, (float("-inf"), float("inf")))[0])
    hi = kpi["metric"].map(lambda m: PHYS_RANGE.get(m, (float("-inf"), float("inf")))[1])
    # lower bound exclusive, upper inclusive
    present_out_of_range = present & ((kpi["value"] <= lo) | (kpi["value"] > hi))
    present_low_r2 = present & kpi["r2"].notna() & (kpi["r2"] < R2_GATE)
    present_zero_r2 = present & kpi["r2"].notna() & (kpi["r2"] == 0)
    present_no_r2_not_pregated = present & _no_r2_not_pregated(kpi)
    return {
        "nat_created_at": int(runs["created_at"].isna().sum()),
        "duplicate_created_at": int(runs["created_at"].duplicated().sum()),
        "present_out_of_range": int(present_out_of_range.sum()),
        "present_low_r2": int(present_low_r2.sum()),
        "present_zero_r2": int(present_zero_r2.sum()),
        "present_no_r2_not_pregated": int(present_no_r2_not_pregated.sum()),
        "n_good": int(present.sum()),
    }


def validate_l2(runs: pd.DataFrame, kpi: pd.DataFrame,
                l1_good: pd.DataFrame | None = None) -> dict[str, int]:
    """Return L2 sanity counts; hard keys should be 0 (n_unresolved, l2_ok_recovered are info).

    Passing l1_good adds the cross-level checks. Counts go to the build log, so a future pull
    that breaks an invariant shows up there instead of being coerced silently.
    """
    # Re-states the gate rule on purpose as a drift tripwire; don't route it through classify().
    ok = kpi["value_status"].eq("ok")
    ok_no_r2_not_pregated = ok & _no_r2_not_pregated(kpi)
    lo = kpi["metric"].map(lambda m: PHYS_RANGE.get(m, (float("-inf"), float("inf")))[0])
    hi = kpi["metric"].map(lambda m: PHYS_RANGE.get(m, (float("-inf"), float("inf")))[1])
    ok_out_of_range = ok & kpi["value"].notna() & ((kpi["value"] <= lo) | (kpi["value"] > hi))
    ok_low_r2 = ok & kpi["r2"].notna() & (kpi["r2"] < R2_GATE)
    # R2 is gated before range, so a cell failing both must be low_r2
    out_of_range_low_r2 = (
        kpi["value_status"].eq("out_of_range") & kpi["r2"].notna() & (kpi["r2"] < R2_GATE)
    )
    low_r2_zero_sentinel = (
        kpi["value_status"].eq("low_r2") & kpi["r2"].notna() & (kpi["r2"] == 0)
    )
    # a 0 on a fit metric is a placeholder: no_fit with an R2, no_result (skipped) without
    fit_zero = kpi["metric"].ne("fRO") & kpi["value"].eq(0)
    fit_zero_mislabelled = fit_zero & kpi["value_status"].ne(
        kpi["r2"].notna().map({True: "no_fit", False: "no_result"})
    )
    unknown_status = ~kpi["value_status"].isin(VALUE_STATUSES)
    unknown_value_source = ~kpi["value_source"].isin(VALUE_SOURCES)
    # an ok cell needs a real value from a free tier; ds1 only ever marks a reject
    ok_null_value = ok & kpi["value"].isna()
    ok_ds1 = ok & kpi["value_source"].eq("ds1")
    free_source_null = kpi["value_source"].isin(["qcaas", "output_info"]) & kpi["value"].isna()
    checks = {
        "nat_created_at": int(runs["created_at"].isna().sum()),
        "duplicate_created_at": int(runs["created_at"].duplicated().sum()),
        "ok_out_of_range": int(ok_out_of_range.sum()),
        "ok_low_r2": int(ok_low_r2.sum()),
        "ok_no_r2_not_pregated": int(ok_no_r2_not_pregated.sum()),
        "out_of_range_low_r2": int(out_of_range_low_r2.sum()),
        "low_r2_zero_sentinel": int(low_r2_zero_sentinel.sum()),
        "fit_zero_mislabelled": int(fit_zero_mislabelled.sum()),
        "unknown_status": int(unknown_status.sum()),
        "unknown_value_source": int(unknown_value_source.sum()),
        "ok_null_value": int(ok_null_value.sum()),
        "ok_ds1": int(ok_ds1.sum()),
        "free_source_null": int(free_source_null.sum()),
        "n_unresolved": int(kpi["value_status"].eq("unresolved").sum()),
    }
    if l1_good is not None:
        checks.update(_cross_level_ok_counts(l1_good, kpi))
    return checks


def _no_r2_not_pregated(kpi: pd.DataFrame) -> pd.Series:
    """Return a mask of fit-metric cells with no R2 that the producer never R2-gated."""
    pregated = pd.Series([r2_pregated(t, m) for t, m in zip(kpi["created_at"], kpi["metric"])],
                         index=kpi.index, dtype=bool)
    return kpi["metric"].ne("fRO") & kpi["r2"].isna() & ~pregated


def _cross_level_ok_counts(l1_good: pd.DataFrame, l2: pd.DataFrame) -> dict[str, int]:
    """Count L1 good values not ok at L2 (hard) and L2 ok cells not good at L1 (info)."""
    keys = ["created_at", "element", "metric"]
    good = l1_good.loc[l1_good["value"].notna(), keys]
    ok = l2.loc[l2["value_status"].eq("ok"), keys]
    merged = good.merge(ok, on=keys, how="outer", indicator=True)
    return {
        "l1_good_not_l2_ok": int((merged["_merge"] == "left_only").sum()),
        "l2_ok_recovered": int((merged["_merge"] == "right_only").sum()),
    }
