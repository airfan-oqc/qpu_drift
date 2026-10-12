import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import decode, periods
from .._io import write_table
from ..paths import OUTPUT_INFO
from ..sources.benchmarking.constants import MEASUREMENT_TYPE_METRIC
from ..sources.benchmarking.load import DERIVED_DIR as DEFAULT_DERIVED
from ..sources.benchmarking.load import KPI_L2_PATH, KPI_SUPPLEMENTED_PATH, load_kpi_l2
from ..sources.benchmarking.output_info import normalise_element
from ..sources.benchmarking.recover import (
    DEFAULT_SPINE,
    read_output_info_ids,
    resolve_and_guard,
)
from ..sources.measurements import local_payloads

PAYLOAD_LINKS = "payload_links.parquet"
PAYLOAD_FITS = "payload_fits.parquet"
SCRIPT_ID = "qpu_drift.derived.supplement:main"

SUPPLEMENT_METRICS = ("T1", "T2", "fRB", "fCX")
SUPPLEMENT_COLS = [
    "payload_value", "payload_error", "payload_r2", "seed_error", "error_source",
    "rb_lengths", "in_incident", "coupling_retired",
]
RB_TYPES = ("RandomizedBenchmarking1q", "RandomizedBenchmarking2q")


def rb_fidelity(measurement_type: str, x0):
    """Return the producer's fidelity from the RB decay constant x0 (maac benchmarking.py).

    1q: 0.5 + 0.5 exp(-1/x0). 2q: CNOT fidelity 0.25 + 0.75 exp(-1/(1.5 x0)), as in DS3 fCX.
    """
    x0 = np.asarray(x0, dtype=float)
    if measurement_type == "RandomizedBenchmarking1q":
        return 0.5 + 0.5 * np.exp(-1 / x0)
    return 0.25 + 0.75 * np.exp(-1 / (1.5 * x0))


def _project(x: np.ndarray, y: np.ndarray, x0: np.ndarray):
    """Return the best A, B and squared error of y = A exp(-x/x0) + B for each x0 in a grid."""
    e = np.exp(-x[:, None] / x0[None, :])
    n = len(x)
    se, see = e.sum(0), (e * e).sum(0)
    sy, sey = y.sum(), (e * y[:, None]).sum(0)
    det = n * see - se * se
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        a = (n * sey - se * sy) / det
        b = (see * sy - se * sey) / det
        sse = ((y[:, None] - (a * e + b)) ** 2).sum(0)
    return a, b, np.where(np.isfinite(sse), sse, np.inf)


def fit_exponential(x, y, x0_guess: float, span: float = 100.0,
                    grid: int = 201, refine: int = 60) -> tuple[float, float, float]:
    """Fit y = A exp(-x/x0) + B by least squares and return (A, x0, B).

    Log-grid search over x0_guess / span .. x0_guess * span, then golden-section refine.
    NaNs if x0_guess isn't positive and finite, or the best grid point is at an edge.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if not (np.isfinite(x0_guess) and x0_guess > 0) or not np.isfinite(y).all():
        return np.nan, np.nan, np.nan
    logs = np.linspace(np.log(x0_guess / span), np.log(x0_guess * span), grid)
    _, _, sse = _project(x, y, np.exp(logs))
    i = int(np.argmin(sse))
    if not np.isfinite(sse[i]) or i in (0, grid - 1):
        return np.nan, np.nan, np.nan
    lo, hi = logs[i - 1], logs[i + 1]
    g = (np.sqrt(5) - 1) / 2
    for _ in range(refine):
        c, d = hi - g * (hi - lo), lo + g * (hi - lo)
        _, _, s = _project(x, y, np.exp(np.array([c, d])))
        if s[0] < s[1]:
            hi = d
        else:
            lo = c
    x0 = float(np.exp((lo + hi) / 2))
    a, b, _ = _project(x, y, np.array([x0]))
    return float(a[0]), x0, float(b[0])


def r_squared(x, y, a: float, x0: float, b: float) -> float:
    """Return 1 - SS_res / SS_tot of y = A exp(-x/x0) + B, the producer's fit quality."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    resid = y - (a * np.exp(-x / x0) + b)
    return float(1 - (resid ** 2).sum() / ((y - y.mean()) ** 2).sum())


def jackknife_se(estimates) -> float:
    """Return the leave-one-out jackknife standard error of an estimate (NaN if any is NaN)."""
    est = np.asarray(estimates, dtype=float)
    n = len(est)
    if n < 2 or not np.isfinite(est).all():
        return np.nan
    return float(np.sqrt((n - 1) / n * ((est - est.mean()) ** 2).sum()))


def rb_seed_curves(measurement_type: str, raw) -> np.ndarray:
    """Return per-seed decay curves (lengths x seeds) from an RB raw_output.

    2q raw_output holds single shots; each seed's P00 is 1/4 + (<z_c> + <z_t> + <z_c z_t>) / 4.
    """
    raw = np.asarray(raw, dtype=float)
    if measurement_type == "RandomizedBenchmarking1q":
        return raw
    zc, zt = raw[:, :, 0, :], raw[:, :, 1, :]
    return 0.25 + (zc.mean(-1) + zt.mean(-1) + (zc * zt).mean(-1)) / 4


def read_fit(node) -> dict | None:
    """Read the exponential fit of one payload target: params (A, x0, B), x, y, stored r2.

    Handles the Fit and FittingBase formats; None if there's no three-parameter exponential fit.
    """
    fit = decode.plain(node) if node is not None else None
    if not isinstance(fit, dict):
        return None
    params = fit.get("opt_vector", fit.get("optimised_results"))
    x = fit.get("xdata", fit.get("x_data"))
    y = fit.get("ydata", fit.get("y_data"))
    if params is None or x is None or y is None or len(params) != 3:
        return None
    if fit.get("function_string") != "y = A*exp(-x/x0) + B":
        return None
    return {
        "params": [float(p) for p in params],
        "x": np.asarray(x, dtype=float),
        "y": np.asarray(y, dtype=float),
        "r2": fit.get("r2"),
    }


def payload_fit_rows(obj: dict) -> list[dict]:
    """Build one row per target of a T1, T2echo, RB1q or RB2q payload.

    payload_r2 is the stored r2, or our own calc_r2 where none is stored. RB rows add
    x0, its fidelity, a refit, curve_match, seed_error (jackknife over seeds) and lengths.
    """
    mtype = obj.get("name")
    header = obj.get("dict", {})
    config = decode.plain(header.get("config")) or {}
    lengths = config.get("lengths") if isinstance(config, dict) else None
    extracted = {r.target: r for r in decode.extract(obj)}
    rows = []
    for target, node in decode.iter_targets(obj):
        res = extracted.get(target)
        fit = read_fit(node.get("fit"))
        row = {
            "result_id": header.get("id"),
            "measurement_type": mtype,
            "target": target,
            "value": _float(res.new) if res else np.nan,
            "error": _float(res.error) if res else np.nan,
            "payload_r2": np.nan,
            "calc_r2": np.nan,
            "x0": np.nan,
            "x0_value": np.nan,
            "refit_x0": np.nan,
            "refit_r2": np.nan,
            "curve_match": pd.NA,
            "seed_error": np.nan,
            "n_seeds": pd.NA,
            "lengths": json.dumps([int(v) for v in lengths]) if mtype in RB_TYPES and lengths is not None else None,
        }
        if fit is not None:
            a, x0, b = fit["params"]
            row["x0"] = x0
            row["calc_r2"] = r_squared(fit["x"], fit["y"], a, x0, b)
            row["payload_r2"] = _float(fit["r2"]) if fit["r2"] is not None else row["calc_r2"]
            if mtype in RB_TYPES:
                row["x0_value"] = float(rb_fidelity(mtype, x0))
                ra, rx0, rb = fit_exponential(fit["x"], fit["y"], x0)
                row["refit_x0"] = rx0
                row["refit_r2"] = r_squared(fit["x"], fit["y"], ra, rx0, rb) if np.isfinite(rx0) else np.nan
                row.update(_seed_error(mtype, node.get("raw_output"), fit, x0))
        rows.append(row)
    return rows


def _float(v) -> float:
    """Return a scalar as float, NaN for None or a non-number."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _seed_error(mtype: str, raw_node, fit: dict, x0: float) -> dict:
    """Return curve_match, n_seeds and the leave-one-seed-out jackknife error of the fidelity."""
    if raw_node is None:
        return {}
    curves = rb_seed_curves(mtype, decode.plain(raw_node))
    if curves.ndim != 2 or curves.shape[0] != len(fit["y"]) or not np.isfinite(curves).all():
        return {}
    n = curves.shape[1]
    total = curves.sum(1)
    loo = [fit_exponential(fit["x"], (total - curves[:, s]) / (n - 1), x0)[1] for s in range(n)]
    return {
        "curve_match": bool(np.allclose(curves.mean(1), fit["y"], rtol=1e-9, atol=1e-12)),
        "n_seeds": n,
        "seed_error": jackknife_se(rb_fidelity(mtype, loo)),
    }


def link_pointers(oi_ids: pd.DataFrame, spine: pd.DataFrame,
                  paths: dict[str, Path]) -> pd.DataFrame:
    """Return every T1 / T2 / fRB / fCX output_info pointer, guarded against the spine.

    One row per pointer, with guarded and the local payload path (None if not local).
    """
    ptr = oi_ids[oi_ids["metric"].isin(SUPPLEMENT_METRICS) & oi_ids["result_id"].notna()].copy()
    ptr["result_id"] = pd.to_numeric(ptr["result_id"]).astype("int64")
    guarded = resolve_and_guard(ptr, spine)
    keys = ["measured_at", "metric", "key", "result_id"]
    out = ptr.merge(guarded[keys + ["relative_filepath"]], on=keys, how="left")
    out["guarded"] = out["relative_filepath"].notna()
    out["path"] = out["relative_filepath"].map(lambda r: str(paths[r]) if r in paths else None)
    return out.reset_index(drop=True)


def read_payload_fits(paths) -> pd.DataFrame:
    """Run payload_fit_rows over many local payloads and add the metric and element keys.

    Unreadable payloads are skipped with one stderr note.
    """
    rows: list[dict] = []
    bad = 0
    for p in sorted(set(paths)):
        try:
            rows.extend(payload_fit_rows(decode.payload(p)))
        except decode.PayloadError:
            bad += 1
    if bad:
        print(f"warning: skipped {bad} unreadable payload(s)", file=sys.stderr)
    fits = pd.DataFrame(rows)
    if fits.empty:
        return fits
    fits["metric"] = fits["measurement_type"].map(MEASUREMENT_TYPE_METRIC)
    fits["element"] = [normalise_element(m, t) for m, t in zip(fits["metric"], fits["target"])]
    return fits


def supplement_kpi(kpi: pd.DataFrame, links: pd.DataFrame,
                   fits: pd.DataFrame) -> pd.DataFrame:
    """Return kpi_enriched plus the DS3 supplement columns (see datasheet 3 and 01_eda.md 3d).

    coupling_retired marks fCX rows after a coupling's last rb2q pointer with no later DS3 value.
    Only a missing result_id is filled; other DS3 columns are untouched, so reruns are idempotent.
    """
    out = kpi.drop(columns=[c for c in SUPPLEMENT_COLS if c in kpi.columns]).copy()
    run_at = out.groupby("created_at")["measured_at"].max()
    out["_run_at"] = out["created_at"].map(run_at)

    fit_cols = ["result_id", "metric", "element", "value", "error", "payload_r2",
                "seed_error", "lengths"]
    f = fits[fit_cols].rename(columns={"value": "payload_value", "error": "payload_error",
                                       "lengths": "rb_lengths"})
    ok = links[links["path"].notna()]
    grouped = ok[ok["element"].isna()].drop(columns="element").merge(
        f, on=["result_id", "metric"], how="inner")
    keyed = ok[ok["element"].notna()].merge(f, on=["result_id", "metric", "element"], how="inner")
    linked = pd.concat([keyed, grouped], ignore_index=True)
    linked = linked.drop_duplicates(["measured_at", "metric", "element"], keep="first")
    linked = linked.rename(columns={"measured_at": "_run_at", "result_id": "_payload_id"})
    keep = ["_run_at", "metric", "element", "_payload_id", "payload_value", "payload_error",
            "payload_r2", "seed_error", "rb_lengths"]
    out = out.merge(linked[keep], on=["_run_at", "metric", "element"], how="left")

    out["result_id"] = out["result_id"].astype("Int64").fillna(out["_payload_id"].astype("Int64"))
    out["error_source"] = np.select(
        [out["error"].notna(), out["payload_error"].notna()], ["ds3", "payload"], "none")
    out["in_incident"] = periods.within(out["created_at"], *(i.period for i in periods.INCIDENTS))

    rb2q = links[(links["metric"] == "fCX") & links["guarded"]]
    last = rb2q.groupby("element")["measured_at"].max()
    early = last[last < rb2q["measured_at"].max()]
    start = out["_run_at"].fillna(out["created_at"])
    after = out["metric"].eq("fCX") & (start > out["element"].map(early))
    served = out.loc[after & out["value"].notna(), "element"].unique()
    out["coupling_retired"] = after & ~out["element"].isin(served)
    return out.drop(columns=["_run_at", "_payload_id"])


def main(argv: list[str] | None = None) -> None:
    """Write kpi_enriched plus the DS3 supplement columns to its own file (01_eda.md 3d).

    Run after build-benchmarking-l2 and decode-ds1; also writes the pointer and payload-fit tables.
    """
    parser = argparse.ArgumentParser(
        description="Write kpi_enriched with payload fit, error, seed error and strata "
                    "columns from the local DS1 payloads to a separate file.")
    parser.add_argument("--kpi", type=Path, default=KPI_L2_PATH,
                        help="Level-2 enriched KPI parquet, read only (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=KPI_SUPPLEMENTED_PATH,
                        help="supplemented KPI parquet to write (default: %(default)s)")
    parser.add_argument("--output-info", type=Path, default=OUTPUT_INFO,
                        help="output_info cache dir (default: %(default)s)")
    parser.add_argument("--spine", type=Path, default=DEFAULT_SPINE,
                        help="clean measurements index (default: %(default)s)")
    parser.add_argument("--roots", type=Path, nargs="+",
                        help="local payload dirs, searched in order (default: "
                             "measurements.PAYLOAD_ROOTS)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DERIVED,
                        help="derived output dir (default: %(default)s)")
    args = parser.parse_args(argv)

    for p in (args.kpi, args.spine):
        if not Path(p).exists():
            sys.exit(f"not found: {p}\nBuild it first (datasheet 3 section 8).")

    if args.out.resolve() == args.kpi.resolve():
        sys.exit("--out must differ from --kpi; supplement-kpi never rewrites kpi_enriched.")

    t0 = time.time()
    kpi = load_kpi_l2(args.kpi)
    paths = local_payloads() if args.roots is None else local_payloads([(r, "") for r in args.roots])
    links = link_pointers(read_output_info_ids(args.output_info),
                          pd.read_parquet(args.spine), paths)
    fits = read_payload_fits(links["path"].dropna())
    out = supplement_kpi(kpi, links, fits)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"script": SCRIPT_ID, "n_links": len(links), "n_fits": len(fits)}
    write_table(links, args.out_dir / PAYLOAD_LINKS, meta)
    write_table(fits, args.out_dir / PAYLOAD_FITS, meta)
    write_table(out, args.out, meta)

    sub = out[out["metric"].isin(SUPPLEMENT_METRICS)]
    print(f"pointers       : {len(links):,} ({int(links['guarded'].sum()):,} guarded, "
          f"{int(links['path'].notna().sum()):,} local)")
    print(f"payload fits   : {len(fits):,} targets from {links['path'].nunique():,} payloads")
    print(f"payload_value  : {int(sub['payload_value'].notna().sum()):,} of {len(sub):,} "
          f"T1/T2/fRB/fCX rows")
    print(f"wrote          : {args.out_dir / PAYLOAD_LINKS}, {args.out_dir / PAYLOAD_FITS}")
    print(f"                 {args.out}  (kpi_enriched plus supplement columns)")
    print(f"took           : {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
