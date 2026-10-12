import json

import numpy as np
import pandas as pd

from .. import decode, periods
from ..sources.benchmarking.clean import gate
from ..sources.hardware_models import load_values, numeric_values, values_at
from ..sources.measurements import local_payloads
from .constants import (
    DRIVE_SCALE_TEMPLATE,
    DS1_1Q_FIT,
    DS1_1Q_MAIN,
    DS3_LAG1_METHOD,
    F01_BIG,
    F01_GAP_MAX,
    F01_REVERT,
    F01_SMALL,
    FIDELITY,
    FINE_LADDER_05,
    FINE_LADDER_05_OFF,
    FLOOR,
    MAIN_METHOD,
    NEAR_FLOOR,
    QUALITY_R2,
    QUALITY_SHORT,
    RB_JUNE,
    RB_JUNE_TAG,
    RB_METRICS,
    SEG_DATE,
    SHORT_LIST_MAX,
)


def stratum_label(t) -> np.ndarray:
    """Return the stratum per time: 'settling' inside SETTLING, else the segment label."""
    seg = pd.Series(periods.segment(t)).map(SEG_DATE).to_numpy()
    return np.where(periods.within(t, *periods.SETTLING), "settling", seg)


def stratum_order(label: str) -> tuple[int, int]:
    """Return a sort key for stratum labels: segment, then the RB 2026-06 split; settling last."""
    if not label[0].isdigit():
        return 99, 0
    return int(label.split()[0]), int(RB_JUNE_TAG.strip() in label)


def kpi_rows(kpi: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Set up B4 rows from kpi_supplemented and the Level-2 runs (B4 section 1).

    Adds run (dense rank of created_at), t (earliest measured_at, else created_at), event masks,
    stratum (fRB and fCX split at 2026-06), the one-gate value_u, r2_u, error_u and status_u,
    cls (ok, censored, absent), use (ok outside INCIDENTS, f01 flags and retired couplings)
    and the fCX list_b4 (payload rb_lengths, else predicted from the previous value and r2).
    """
    k = kpi.merge(runs[["created_at", "gating_regime"]], on="created_at", how="left")
    k["run"] = k["created_at"].rank(method="dense").astype(int) - 1
    k["t"] = k["run"].map(k.groupby("run")["measured_at"].min()).fillna(k["created_at"])
    qa = k["element"].str.split("-").str[0].astype(int)
    qb = k["element"].str.split("-").str[1].astype(float).fillna(-1).astype(int)
    k["incident"] = periods.within(k["t"], *[i.period for i in periods.INCIDENTS])
    k["f01_flag"] = periods.f01_flagged(k["t"], qa) | periods.f01_flagged(k["t"], qb)  # 2q: control or target
    k["settling"] = periods.within(k["t"], *periods.SETTLING)
    k["segment"] = periods.segment(k["t"])
    k["stratum"] = np.where(k["settling"], "settling", k["segment"].map(SEG_DATE))
    june = k["metric"].isin(RB_METRICS) & (k["t"] >= RB_JUNE) & ~k["settling"]
    k.loc[june, "stratum"] = k.loc[june, "stratum"] + RB_JUNE_TAG

    k["value_u"] = k["value"].fillna(k["payload_value"])
    k["r2_u"] = k["r2"].fillna(k["payload_r2"])
    k["error_u"] = k["error"].fillna(k["payload_error"])
    k["source_u"] = np.select([k["value"].notna(), k["payload_value"].notna()], ["ds3", "payload"], "none")
    # an L2 ok with no R2 was pregated (era-1 producer, off the output_info cache), so keep it pregated
    status = pd.Series([gate(m, v, r, pregated=s == "ok")
                        for m, v, r, s in zip(k["metric"], k["value_u"], k["r2_u"], k["value_status"])],
                       index=k.index)
    k["status_u"] = status.fillna(k["value_status"])
    k["cls"] = np.select([k["status_u"] == "ok", k["status_u"].isin(["low_r2", "out_of_range", "no_fit"])],
                         ["ok", "censored"], "absent")
    k["use"] = (k["cls"] == "ok") & ~k["incident"] & ~k["f01_flag"] & ~k["coupling_retired"].fillna(False).astype(bool)

    fc = k[k["metric"] == "fCX"].sort_values(["element", "run"])
    last = fc[["value_u", "r2_u"]].where(fc["value_u"].notna()).groupby(fc["element"]).shift()
    last = last.groupby(fc["element"]).ffill()
    quality = np.where(last["r2_u"].isna() | (last["r2_u"] > QUALITY_R2), np.floor(100 * last["value_u"]), 50)
    pred = pd.Series(np.where(last["value_u"].isna(), None, np.where(quality < QUALITY_SHORT, "short", "long")),
                     index=fc.index)
    n_len = k["rb_lengths"].dropna().map(lambda s: len(json.loads(s)))
    lst = n_len.map(lambda n: "short" if n <= SHORT_LIST_MAX else "long").reindex(k.index).where(k["metric"] == "fCX")
    k["list_b4"] = lst.fillna(pred.reindex(k.index)).where(k["metric"] == "fCX")
    return k


def censored_share(k: pd.DataFrame) -> pd.Series:
    """Return the censored share of ok + censored rows per metric and element.

    A lower bound (B4 section 2). Skips incidents, f01 flags, retired couplings and era-0 fits.
    """
    era0_fit = (k["gating_regime"] == "era0_ungated") & (k["metric"] != "fRO")  # no r2 anywhere, only range judged
    base = k[~k["incident"] & ~k["f01_flag"] & ~k["coupling_retired"].fillna(False).astype(bool) & ~era0_fit]
    att = base[base["cls"] != "absent"]
    return att.groupby(["metric", "element"])["cls"].agg(lambda s: (s == "censored").mean()).rename("censored_share")


def kept_kpi(k: pd.DataFrame) -> pd.DataFrame:
    """Return the B4 kept rows (use) with the stage C columns.

    x is T1/T2 in us or the infidelity 1 - F, lx = log10 x; unit and regime carry the fCX list.
    """
    kept = k[k["use"]].copy()
    fid = kept["metric"].isin(FIDELITY)
    kept["x"] = np.where(fid, 1 - kept["value_u"], kept["value_u"] * 1e6)
    kept["lx"] = np.log10(kept["x"].where(kept["x"] > 0))
    floor = kept["metric"].map(FLOOR)
    kept["near_floor"] = floor.notna() & (kept["value_u"] <= floor + NEAR_FLOOR)
    kept["lst"] = kept["list_b4"].fillna("-")
    has_list = kept["lst"] != "-"
    kept["unit"] = np.where(has_list, kept["element"] + " " + kept["lst"], kept["element"])
    kept["regime"] = np.where(has_list, kept["stratum"] + ", " + kept["lst"] + " list", kept["stratum"])
    return kept


def ds3_noise(noise: pd.DataFrame) -> pd.DataFrame:
    """Return the main and lag-1 noise sigma per DS3 metric, element and regime (B4 noise rows).

    sigma_main uses the first MAIN_METHOD the row has; main_method names it.
    """
    d = noise[noise["source"] == "ds3"]
    piv = d.pivot_table(index=["param", "entity", "regime"], columns="method", values="sigma", aggfunc="first")
    out = pd.DataFrame(index=piv.index)
    out["sigma_main"], out["main_method"] = np.nan, None
    metric = out.index.get_level_values("param")
    for mt, methods in MAIN_METHOD.items():
        for m in methods:
            if m not in piv:
                continue
            fill = (metric == mt) & out["sigma_main"].isna().to_numpy() & piv[m].notna().to_numpy()
            out.loc[fill, "sigma_main"] = piv.loc[fill, m]
            out.loc[fill, "main_method"] = m
    out["sigma_lag1"] = piv[DS3_LAG1_METHOD] if DS3_LAG1_METHOD in piv else np.nan
    return out.rename_axis(["metric", "element", "regime"])


def ds1_1q_noise(noise: pd.DataFrame) -> pd.DataFrame:
    """Return the B3c 1q noise per param (no '1q.' prefix), qubit and stratum.

    sigma is the fit sd (high end); sigma_main the first DS1_1Q_MAIN row present, named by main_method.
    """
    d = noise[noise["source"] == "ds1_1q"]
    d = d.assign(param=d["param"].str.removeprefix("1q."), qubit=d["entity"].astype(int), stratum=d["regime"])
    key = ["param", "qubit", "stratum"]
    fit = d[d["method"].isin(DS1_1Q_FIT)].set_index(key)[["sigma", "n"]]
    rank = d["method"].map({m: i for i, m in enumerate(DS1_1Q_MAIN)})
    main = (d[rank.notna()].assign(rank=rank).sort_values("rank").groupby(key).first()
            [["sigma", "method"]].rename(columns={"sigma": "sigma_main", "method": "main_method"}))
    return fit.join(main, how="outer").sort_index()


def onevq_rows(ent: pd.DataFrame, runs1q: pd.DataFrame) -> pd.DataFrame:
    """Return applied 1q values: one row per calibrate_1q run, qubit and param the run changed.

    value is the run-map new (B3c); adds run, t, snap_after, incident and f01_flag masks and stratum.
    """
    e = ent[ent["qubit"].notna() & ent["changed"].fillna(False).astype(bool) & ent["new"].notna()]
    e = e.merge(runs1q[["start", "snap_after"]], on="start", how="left", validate="many_to_one")
    order = pd.Series(np.arange(runs1q["start"].nunique()), index=np.sort(runs1q["start"].unique()))
    e = e.assign(qubit=e["qubit"].astype(int), run=e["start"].map(order), t=e["start"], value=e["new"])
    e["incident"] = periods.within(e["t"], *[i.period for i in periods.INCIDENTS])
    e["f01_flag"] = periods.f01_flagged(e["t"], e["qubit"])
    e["stratum"] = stratum_label(e["t"])
    return e.reset_index(drop=True)


def _undoes(a: pd.Series, b: pd.Series) -> pd.Series:
    """True where two consecutive changes have opposite signs and nearly cancel."""
    return (np.sign(a) != np.sign(b)) & ((a + b).abs() < F01_REVERT * np.maximum(a.abs(), b.abs()))


def f01_moves(rows: pd.DataFrame, gap_max: pd.Timedelta = F01_GAP_MAX) -> pd.DataFrame:
    """Label each applied f01 change per qubit and segment (d2a section 1).

    rows needs qubit, seg, t, y_hz and warned. A gap over gap_max starts a new segment. Adds d (kHz),
    d_prev, d_next, pos, size, kind (holds, out or back for large moves), ctrl and quiet.
    """
    f = rows.sort_values(["qubit", "t"], ignore_index=True)
    gap = (f.groupby("qubit")["t"].diff() > gap_max).groupby(f["qubit"]).cumsum()
    f["seg"] = f["seg"].astype(str) + " g" + gap.astype(str)

    grp = f.groupby(["qubit", "seg"])
    f["d"] = grp["y_hz"].diff() / 1e3
    f["d_prev"] = f.groupby(["qubit", "seg"])["d"].shift(1)
    f["d_next"] = f.groupby(["qubit", "seg"])["d"].shift(-1)
    f["pos"] = grp.cumcount()

    # ctrl: a warned run among runs i-2 to i+1, whose applied values set d_prev, d and d_next
    w = f["warned"].astype("boolean").fillna(False).astype(bool)
    wg = w.groupby([f["qubit"], f["seg"]])
    f["warned"] = w
    f["ctrl"] = w.copy()
    for k in (2, 1, -1):
        f["ctrl"] |= wg.shift(k).astype("boolean").fillna(False).astype(bool)

    ad = f["d"].abs()
    labels = [f"<{F01_SMALL:g}", f"{F01_SMALL:g}-{F01_BIG:g}", f">={F01_BIG:g}"]
    f["size"] = np.select([ad < F01_SMALL, ad < F01_BIG, ad >= F01_BIG], labels, "")
    out, back = _undoes(f["d"], f["d_next"]), _undoes(f["d_prev"], f["d"])
    f["kind"] = np.select([ad < F01_BIG, out, back], ["", "out", "back"], "holds")
    f.loc[f["d"].isna(), "kind"] = ""
    small = (ad < F01_SMALL) & (f["d_prev"].abs() < F01_SMALL) & (f["d_next"].abs() < F01_SMALL)
    f["quiet"] = small & ~f["ctrl"]
    return f


def fine_freq_stage(results: pd.DataFrame) -> pd.DataFrame:
    """Count how far each calibrate_1q run got up the FineFrequency ladder, per qubit.

    n_freq is the accepted steps (0 = Ramsey applied); stage is 0 for the full ladder, 1 for one
    step short, 2 for two or more (full ladder is 3 steps from FINE_LADDER_05, else 2).
    """
    r = results[(results["routine"] == "calibrate_1q") & results["key"].str.fullmatch(r"Q\d+")]
    n = r[r["step"] == "freq"].groupby(["start", "key"]).size()
    n = n.reindex(r.groupby(["start", "key"]).size().index, fill_value=0).rename("n_freq").reset_index()
    n["qubit"] = n["key"].str[1:].astype(int)
    full = np.where((n["start"] >= FINE_LADDER_05) & ~periods.within(n["start"], FINE_LADDER_05_OFF), 3, 2)
    n["stage"] = np.minimum(full - n["n_freq"], 2).clip(0)
    return n[["start", "qubit", "n_freq", "stage"]]


def drive_scale(values: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return the DS2 drive scale per qubit (real part) and its validity window."""
    sc = load_values(path_template=DRIVE_SCALE_TEMPLATE) if values is None else values
    sc = sc.assign(qubit=sc["path"].str.extract(r"\{Ch\d+\.Q(\d+)\.drive\}")[0]).dropna(subset=["qubit"])
    z = numeric_values(sc)
    return (pd.DataFrame({"qubit": sc["qubit"].astype(int), "valid_from_ts": sc["valid_from_ts"].astype("datetime64[ns]"),
                          "valid_to_ts": sc["valid_to_ts"].astype("datetime64[ns]"), "scale": z.to_numpy().real})
            .sort_values("valid_from_ts", ignore_index=True))


def scale_at(t: pd.Series, qubit: pd.Series, scale: pd.DataFrame) -> pd.Series:
    """Return the drive scale in effect per time and qubit; NaN where the path is absent (B3c)."""
    at = pd.DataFrame({"t": t, "qubit": qubit.astype(int)})
    return values_at(at, scale, ["qubit"], col="scale")


def amp_x_scale(rows: pd.DataFrame, scale: pd.DataFrame) -> pd.DataFrame:
    """Turn the amp rows of onevq_rows into param amp_x_scale: ln |amp x drive scale at snap_after|.

    Rows without a drive scale (outside DS2) are dropped.
    """
    a = rows[rows["param"] == "amp"].copy()
    a["scale"] = scale_at(a["snap_after"], a["qubit"], scale)
    a = a.dropna(subset=["scale"])
    a["amp"] = a["value"]
    a["value"] = np.log(np.abs(a["amp"] * a["scale"]))
    return a[np.isfinite(a["value"])].assign(param="amp_x_scale")


def freq_shift_rows(measurements: pd.DataFrame, run_results: pd.DataFrame) -> pd.DataFrame:
    """Decode FineFreqShift results per calibrate_1q run and qubit from local payloads (B3c).

    Keeps only targets the run claimed at step freq_shift; skips missing or undecodable payloads.
    """
    rr = run_results[run_results["valid"] & (run_results["step"] == "freq_shift")]
    claimed = set(zip(rr["result_id"], rr["key"]))
    claim = rr.drop_duplicates("result_id").set_index("result_id")[["start"]]
    fs = measurements[measurements["measurement_type"] == "FineFreqShift"].join(claim, on="id", how="inner")
    paths = local_payloads()
    rows = []
    for r in fs.itertuples():
        path = paths.get(r.relative_filepath)
        if path is None:
            continue
        try:
            obj = decode.payload(path)
        except decode.PayloadError:
            continue
        rows += [(r.id, r.start, res.target, res.old, res.new, res.error) for res in decode.extract(obj)
                 if res.param is not None and (r.id, res.target) in claimed]
    out = pd.DataFrame(rows, columns=["id", "start", "target", "old", "new", "sd"])
    out["qubit"] = out["target"].str.extract(r"^Q(\d+)$")[0].astype("Int64")
    out[["old", "new", "sd"]] = out[["old", "new", "sd"]].apply(pd.to_numeric, errors="coerce")
    return out.dropna(subset=["qubit"]).astype({"qubit": int}).drop(columns="target").reset_index(drop=True)


def register_events() -> pd.DataFrame:
    """Return the periods.py register as in B4 section 5: one row per event time, type and label."""
    ev = ([(b.at, "BREAK", f"BREAK {b.at:%Y-%m-%d} {b.what}") for b in periods.BREAKS]
          + [(a, "OUTAGE", f"OUTAGE {w}") for p in periods.OUTAGES for a, w in zip(p, ("start", "end"))]
          + [(a, "DS4_GAP", f"DS4_GAP {w}") for p in periods.DS4_GAPS for a, w in zip(p, ("start", "end"))]
          + [(u.at, "UNTRACKED_DS2", "UNTRACKED_DS2") for u in periods.UNTRACKED_DS2]
          + [(a, "UNTRACKED_DS2_OTHER", "UNTRACKED_DS2_OTHER") for a in periods.UNTRACKED_DS2_OTHER]
          + [(a, "F01_FLAG", f"F01_FLAG {w}") for f in periods.F01_FLAGS for a, w in zip(f.period, ("start", "end"))]
          + [(a, "INCIDENT", f"INCIDENT {w}") for i in periods.INCIDENTS for a, w in zip(i.period, ("start", "end"))]
          + [(a, "THERMAL_CYCLE", f"THERMAL_CYCLE {w}") for c in periods.THERMAL_CYCLES for a, w in zip(c, ("start", "end"))])
    out = pd.DataFrame(ev, columns=["at", "type", "label"])
    out["at"] = pd.to_datetime(out["at"]).astype("datetime64[ns]")
    return out.sort_values("at", ignore_index=True)
