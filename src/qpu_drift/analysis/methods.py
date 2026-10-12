from functools import cache
from statistics import NormalDist

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .. import periods
from .constants import MAX_GAP_PER_LAG, MIN_N


def rsd(x) -> float:
    """Return the robust sd, 1.4826 x MAD of the finite values (NaN below 2)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return np.nan
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def lag_pairs(df: pd.DataFrame, keys: list[str], lag: int, col: str = "y",
              max_gap: pd.Timedelta = MAX_GAP_PER_LAG, cuts=()) -> pd.DataFrame:
    """Pair values lag runs apart in the same series, with d = value_1 - value_0.

    Keeps pairs at most lag x max_gap apart with no cut time in (t_0, t_1].
    """
    a = df[[*keys, "run", "t", col]]
    m = a.merge(a.assign(run=a["run"] - lag), on=[*keys, "run"], suffixes=("_0", "_1"))
    keep = (m["t_1"] - m["t_0"]) <= max_gap * lag
    cuts = list(cuts)
    if cuts and len(m):
        keep &= ~periods.spans(m["t_0"], m["t_1"], cuts)
    m = m[keep].rename(columns={"run": "run_0"})
    return m.assign(d=m[f"{col}_1"] - m[f"{col}_0"])


def structure_function(df: pd.DataFrame, keys: list[str], lags, col: str = "y",
                       max_gap: pd.Timedelta = MAX_GAP_PER_LAG, cuts=(), min_pairs: int = MIN_N) -> pd.DataFrame:
    """Return the lag-L spread per series: pair count, rsd / sqrt(2) and median days apart.

    rsd is of the lag-L difference. Series-lags with fewer than min_pairs pairs are dropped.
    """
    out = []
    for lag in lags:
        p = lag_pairs(df, keys, lag, col, max_gap, cuts)
        if p.empty:
            continue
        p = p.assign(days=(p["t_1"] - p["t_0"]) / pd.Timedelta("1D"))
        g = p.groupby(keys).agg(pairs=("d", "size"), sigma=("d", lambda d: rsd(d) / np.sqrt(2)), days=("days", "median"))
        out.append(g[g["pairs"] >= min_pairs].assign(lag=lag))
    return pd.concat(out).set_index("lag", append=True).sort_index()


def block_length(var_ratio: pd.Series, tol: float = 0.1) -> int:
    """Pick the block length from a variance ratio indexed by lag (C1 section 2).

    Returns the first lag within tol of the last lag's ratio, or the last lag if it still
    rises by more than tol.
    """
    v = var_ratio.dropna().sort_index()
    if v.iloc[-1] > (1 + tol) * v.iloc[-2]:
        return int(v.index[-1])
    return int(v.index[(v >= v.iloc[-1] / (1 + tol)).to_numpy().argmax()])


def _nonneg_two(basis: np.ndarray, r: np.ndarray) -> tuple[float, float, float]:
    """Fit r = c + a basis by least squares with c, a >= 0; return c, a and the sum of squares."""
    x = np.column_stack([np.ones_like(basis), basis])
    (c, a), *_ = np.linalg.lstsq(x, r, rcond=None)
    if a < 0:
        c, a = max(r.mean(), 0.0), 0.0
    elif c < 0:
        c, a = 0.0, max(float(basis @ r / (basis @ basis)), 0.0)
    return float(c), float(a), float(((r - c - a * basis) ** 2).sum())


def _aicc(sse: float, n: int, k: int) -> float:
    """Return the small-sample AIC of a least-squares fit with k parameters (NaN if n <= k + 1)."""
    if n <= k + 1:
        return np.nan
    return float(n * np.log(max(sse / n, 1e-300)) + 2 * k + 2 * k * (k + 1) / (n - k - 1))


def fit_timescale(lag, ratio, taus=None) -> dict:
    """Fit ratio(L) = c + a (1 - exp(-L / tau)) on a tau grid with c, a >= 0.

    ratio is the signal ratio at lag L. In noise units, c is the part new at every run (1 if the
    noise estimate is right) and a the correlated part; tau is in lags.
    Also fits a random walk c + b L and a constant. best has the lowest AICc, not sum of squares,
    since for L << tau the exponential becomes the random walk. tau_at_edge means no plateau.
    """
    lag, ratio = np.asarray(lag, dtype=float), np.asarray(ratio, dtype=float)
    ok = np.isfinite(lag) & np.isfinite(ratio)
    lag, ratio = lag[ok], ratio[ok]
    taus = np.geomspace(0.25, 1000, 400) if taus is None else np.asarray(taus, dtype=float)
    best = min((_nonneg_two(1 - np.exp(-lag / tau), ratio) + (tau,) for tau in taus), key=lambda f: f[2])
    rw = _nonneg_two(lag, ratio)
    flat = float(((ratio - ratio.mean()) ** 2).sum())
    n = len(lag)
    aicc = {"exp": _aicc(best[2], n, 3), "rw": _aicc(rw[2], n, 2), "flat": _aicc(flat, n, 1)}
    finite = {m: v for m, v in aicc.items() if np.isfinite(v)}
    return {"points": int(n), "c": best[0], "a": best[1], "tau": best[3], "sse": best[2],
            "tau_at_edge": bool(best[3] >= taus.max()), "rw_c": rw[0], "rw_b": rw[1], "rw_sse": rw[2],
            "flat_sse": flat, "aicc": aicc["exp"], "rw_aicc": aicc["rw"], "flat_aicc": aicc["flat"],
            "best": min(finite, key=finite.get) if finite else None}


def theil_sen(x, y) -> tuple[float, float]:
    """Return the Theil-Sen slope and intercept of y on x (NaN below 3 points)."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3:
        return np.nan, np.nan
    i, j = np.triu_indices(len(x), 1)
    dx = x[j] - x[i]
    use = dx != 0
    slope = float(np.median((y[j] - y[i])[use] / dx[use]))
    return slope, float(np.median(y - slope * x))


def long_run_sd(e, bandwidth: int) -> float:
    """Return the long-run sd of a series (Bartlett kernel, bandwidth in samples)."""
    e = np.asarray(e, dtype=float)
    e = e - e.mean()
    n = len(e)
    s = e @ e / n
    for j in range(1, min(bandwidth, n - 1) + 1):
        s += 2 * (1 - j / (bandwidth + 1)) * (e[j:] @ e[:-j]) / n
    return float(np.sqrt(max(s, 0.0)))


def ols_slope_hac(x, y, bandwidth: int) -> tuple[float, float]:
    """Return the OLS slope of y on x and its Newey-West standard error.

    Bartlett kernel; bandwidth is in samples.
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    xc = x - x.mean()
    sxx = xc @ xc
    if len(x) < 3 or sxx == 0:
        return np.nan, np.nan
    b = float(xc @ (y - y.mean()) / sxx)
    u = xc * (y - y.mean() - b * xc)
    s = u @ u
    for j in range(1, min(bandwidth, len(u) - 1) + 1):
        s += 2 * (1 - j / (bandwidth + 1)) * (u[j:] @ u[:-j])
    return b, float(np.sqrt(max(s, 0.0)) / sxx)


@cache
def hac_crit(n: int, bandwidth: int, level: float = 0.95, reps: int = 4000, seed: int = 0) -> float:
    """Simulate the critical |slope / Newey-West error| for n evenly spaced white-noise points.

    About 1.96 when bandwidth / n is small, larger otherwise (fixed-b).
    """
    if n < 4:
        return np.nan
    rng = np.random.default_rng(seed)
    xc = np.arange(n, dtype=float) - (n - 1) / 2
    sxx = xc @ xc
    e = rng.standard_normal((reps, n))
    e -= e.mean(axis=1, keepdims=True)
    b = e @ xc / sxx
    u = xc * (e - b[:, None] * xc)
    s = np.einsum("ij,ij->i", u, u)
    for j in range(1, min(bandwidth, n - 1) + 1):
        s += 2 * (1 - j / (bandwidth + 1)) * np.einsum("ij,ij->i", u[:, j:], u[:, :-j])
    z = b * sxx / np.sqrt(np.maximum(s, 1e-300))
    return float(np.quantile(np.abs(z), level))


def chip_trend(t, y, bandwidth: int) -> dict:
    """Fit the trend of one chip-wide series (one value per run) with a HAC error.

    Returns the slope per day, HAC error, z, crit and 95% interval. Use this over a bootstrap
    across elements when they share a common mode.
    """
    t = pd.DatetimeIndex(t)
    y = np.asarray(y, dtype=float)
    days = np.asarray((t - t.min()) / pd.Timedelta("1D"), dtype=float)
    ts, _ = theil_sen(days, y)
    b, se = ols_slope_hac(days, y, bandwidth)
    crit = hac_crit(len(y), int(bandwidth))
    return {"runs": len(y), "span_days": float(days.max()) if len(days) else np.nan, "blocks": len(y) / bandwidth,
            "ts_slope": ts, "ols_slope": b, "se": se, "z": b / se if se > 0 else np.nan, "crit": crit,
            "lo": b - crit * se, "hi": b + crit * se}


def combine_trends(tr: pd.DataFrame) -> dict:
    """Return the inverse-variance mean of chip_trend OLS slopes from independent strata.

    Errors are scaled by crit / 1.96 first; p_het is Cochran's Q test for one shared slope.
    """
    g = tr.dropna(subset=["ols_slope", "se", "crit"])
    g = g[g["se"] > 0]
    if g.empty:
        return {"strata": 0, "runs": 0, "ols_slope": np.nan, "se": np.nan, "z": np.nan, "lo": np.nan, "hi": np.nan,
                "p_het": np.nan}
    se = g["se"] * g["crit"] / 1.96
    w = 1 / se ** 2
    b = float((w * g["ols_slope"]).sum() / w.sum())
    s = float(1 / np.sqrt(w.sum()))
    q, df = float((w * (g["ols_slope"] - b) ** 2).sum()), len(g) - 1
    p_het = (1 - NormalDist().cdf(((q / df) ** (1 / 3) - (1 - 2 / (9 * df))) / np.sqrt(2 / (9 * df)))
             if df > 0 else np.nan)
    return {"strata": len(g), "runs": int(g["runs"].sum()), "ols_slope": b, "se": s, "z": b / s,
            "lo": b - 1.96 * s, "hi": b + 1.96 * s, "p_het": p_het}


def common_mode_share(m: pd.DataFrame, min_units: int, n_null: int = 200, seed: int = 0) -> dict:
    """Return the common-mode variance share of a runs x elements panel, with a null.

    The null circularly shifts each element by a random offset. Returns share, null median
    and q95, excess and p.
    """
    x = m.to_numpy(dtype=float)
    unit = np.nanmedian([rsd(c) for c in x.T])

    def share(a: np.ndarray) -> float:
        cnt = np.isfinite(a).sum(axis=1)
        with np.errstate(all="ignore"):
            med = np.nanmedian(a[cnt >= min_units], axis=1) if (cnt >= min_units).any() else np.array([])
        return (rsd(med) / unit) ** 2 if len(med) >= 3 and unit > 0 else np.nan

    obs = share(x)
    rng = np.random.default_rng(seed)
    null = np.array([share(np.column_stack([np.roll(c, rng.integers(len(c))) for c in x.T])) for _ in range(n_null)])
    null = null[np.isfinite(null)]
    if not len(null):
        return {"share": obs, "null_median": np.nan, "null_q95": np.nan, "excess": np.nan, "p": np.nan}
    return {"share": obs, "null_median": float(np.median(null)), "null_q95": float(np.quantile(null, 0.95)),
            "excess": obs / float(np.median(null)), "p": float((1 + (null >= obs).sum()) / (1 + len(null)))}


def perm_p(null, observed) -> float:
    """Return the permutation p (1 + null values >= observed) / (1 + null values), never 0."""
    null = np.asarray(null, dtype=float)
    null = null[np.isfinite(null)]
    return float((1 + (null >= observed).sum()) / (1 + len(null))) if np.isfinite(observed) else np.nan


def bh(p) -> np.ndarray:
    """Return Benjamini-Hochberg q-values (NaN kept)."""
    p = np.asarray(p, dtype=float)
    q = np.full(len(p), np.nan)
    ok = np.flatnonzero(np.isfinite(p))
    if len(ok):
        o = ok[np.argsort(p[ok])]
        r = p[o] * len(o) / np.arange(1, len(o) + 1)
        q[o] = np.minimum(np.minimum.accumulate(r[::-1])[::-1], 1.0)
    return q


def cusum_split(y, min_seg: int, bandwidth: int) -> tuple[int, float]:
    """Return the best single split k of y (y[:k] | y[k:]) and its CUSUM statistic.

    The statistic is in long-run sd units; with no change it follows the sup of a Brownian bridge.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    s = np.cumsum(y - y.mean())
    ks = np.arange(min_seg, n - min_seg + 1)
    if len(ks) == 0:
        return -1, np.nan
    k = int(ks[np.abs(s[ks - 1]).argmax()])
    resid = np.concatenate([y[:k] - y[:k].mean(), y[k:] - y[k:].mean()])
    sigma = long_run_sd(resid, bandwidth)
    return k, float(abs(s[k - 1]) / (sigma * np.sqrt(n))) if sigma > 0 else np.nan


def binary_segmentation(y, min_seg: int, bandwidth: int, crit: float | None = None) -> pd.DataFrame:
    """Find change points of y by binary segmentation with the cusum_split test.

    Keeps splitting while the statistic beats crit (None uses cusum_crit per segment).
    Returns one row per change point.
    """
    y = np.asarray(y, dtype=float)
    out, todo = [], [(0, len(y))]
    while todo:
        a, b = todo.pop()
        if b - a < 2 * min_seg:
            continue
        k, stat = cusum_split(y[a:b], min_seg, bandwidth)
        c = cusum_crit(b - a, min_seg, bandwidth) if crit is None else crit
        if np.isfinite(stat) and stat > c:
            out.append({"split": a + k, "stat": stat, "crit": c, "seg_start": a, "seg_end": b,
                        "shift": float(y[a + k:b].mean() - y[a:a + k].mean())})
            todo += [(a, a + k), (a + k, b)]
    return pd.DataFrame(out, columns=["split", "stat", "crit", "seg_start", "seg_end", "shift"]).sort_values(
        "split", ignore_index=True)


@cache
def cusum_crit(n: int, min_seg: int, bandwidth: int, level: float = 0.95, reps: int = 2000, seed: int = 0) -> float:
    """Simulate the critical value of the cusum_split statistic for n white-noise values.

    Close to KOLMOGOROV_95 only when the bandwidth is a small share of n (fixed-b).
    """
    rng = np.random.default_rng(seed)
    e = rng.standard_normal((reps, n))
    e -= e.mean(axis=1, keepdims=True)
    s = np.cumsum(e, axis=1)
    ks = np.arange(min_seg, n - min_seg + 1)
    if len(ks) == 0:
        return np.nan
    k = ks[np.abs(s[:, ks - 1]).argmax(axis=1)]
    sk = s[np.arange(reps), k - 1]
    resid = e - np.where(np.arange(n)[None, :] < k[:, None], (sk / k)[:, None], (-sk / (n - k))[:, None])
    v = np.einsum("ij,ij->i", resid, resid) / n
    for j in range(1, min(bandwidth, n - 1) + 1):
        v += 2 * (1 - j / (bandwidth + 1)) * np.einsum("ij,ij->i", resid[:, j:], resid[:, :-j]) / n
    stat = np.abs(sk) / (np.sqrt(np.maximum(v, 1e-300)) * np.sqrt(n))
    return float(np.quantile(stat, level))


def _scan_stat(y: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Return the full-split indices, window steps there and their robust sd."""
    steps = window_steps(y, k)
    idx = np.flatnonzero(np.isfinite(steps))
    return idx, steps[idx], (rsd(steps[idx]) if len(idx) >= 3 else np.nan)


@cache
def scan_crit(n: int, k: int, level: float = 0.95, reps: int = 1000, seed: int = 0) -> float:
    """Simulate the critical value of max |D_i| / rsd(D) in scan_jumps on a Gaussian random walk."""
    if n < 2 * k + 2:
        return np.nan
    rng = np.random.default_rng(seed)
    y = np.cumsum(rng.standard_normal((reps, n)), axis=1)
    med = np.median(sliding_window_view(y, k, axis=1), axis=2)
    i = np.arange(k, n - k + 1)
    d = med[:, i] - med[:, i - k]
    mad = 1.4826 * np.median(np.abs(d - np.median(d, axis=1, keepdims=True)), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):  # tied medians can give mad 0
        return float(np.quantile(np.abs(d).max(axis=1) / mad, level))


def scan_jumps(y, k: int, crit: float | None = None) -> pd.DataFrame:
    """Find level jumps in a no-plateau series, where the CUSUM test over-splits plain wander.

    A split is a jump where |D_i| / rsd(D) of the k-run window step exceeds crit (None: scan_crit);
    of jumps closer than k runs the largest is kept.
    """
    cols = ["split", "stat", "crit", "shift"]
    y = np.asarray(y, dtype=float)
    idx, steps, s = _scan_stat(y, k)
    crit = scan_crit(len(y), k) if crit is None else crit
    if not (s > 0 and np.isfinite(crit)):
        return pd.DataFrame(columns=cols)
    z = np.abs(steps) / s
    taken = []
    for m in np.argsort(-z, kind="stable"):
        if z[m] <= crit:
            break
        if all(abs(idx[m] - idx[i]) >= k for i in taken):
            taken.append(m)
    return pd.DataFrame([{"split": int(idx[m]), "stat": float(z[m]), "crit": crit, "shift": float(steps[m])}
                         for m in sorted(taken, key=lambda m: idx[m])], columns=cols)


def window_steps(y, k: int) -> np.ndarray:
    """Return the median of the next k values minus the median of the previous k at each split.

    Entry i is the split before y[i] (len(y) + 1 entries); NaN where a window isn't full.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    out = np.full(n + 1, np.nan)
    if n < 2 * k:
        return out
    med = np.median(sliding_window_view(y, k), axis=1)  # med[s] = median(y[s:s + k])
    i = np.arange(k, n - k + 1)
    out[i] = med[i] - med[i - k]
    return out


def bootstrap_median(values, n_boot: int = 2000, level: float = 0.95, seed: int = 0) -> tuple[float, float, float]:
    """Return the median and a percentile bootstrap interval (one value per independent unit)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 3:
        return (float(np.median(v)) if len(v) else np.nan), np.nan, np.nan
    rng = np.random.default_rng(seed)
    boot = np.median(rng.choice(v, size=(n_boot, len(v)), replace=True), axis=1)
    lo, hi = np.quantile(boot, [(1 - level) / 2, (1 + level) / 2])
    return float(np.median(v)), float(lo), float(hi)


def near(times, at, window: pd.Timedelta) -> np.ndarray:
    """Return a bool per time: True if some time in at lies within window of it."""
    t = np.asarray(pd.DatetimeIndex(times).values, dtype="datetime64[ns]")
    a = np.sort(np.asarray(pd.DatetimeIndex(list(at)).values, dtype="datetime64[ns]"))
    if len(a) == 0 or len(t) == 0:
        return np.zeros(len(t), dtype=bool)
    i = np.searchsorted(a, t)
    w = np.timedelta64(int(window.value), "ns")
    left = np.abs(t - a[np.clip(i - 1, 0, len(a) - 1)]) <= w
    right = np.abs(a[np.clip(i, 0, len(a) - 1)] - t) <= w
    return left | right


def series_trends(df: pd.DataFrame, keys: list[str], col: str = "y", block_col: str = "block") -> pd.DataFrame:
    """Fit per-series Theil-Sen and OLS slopes per day with a HAC error, z and its crit (hac_crit).

    block_col (constant per series) is the bandwidth in runs.
    """
    out = []
    for key, g in df.dropna(subset=[col]).sort_values("run").groupby(keys):
        days = ((g["t"] - g["t"].iloc[0]) / pd.Timedelta("1D")).to_numpy()
        ts, _ = theil_sen(days, g[col])
        bw = int(g[block_col].iloc[0])
        b, se = ols_slope_hac(days, g[col].to_numpy(), bw)
        out.append(dict(zip(keys, key if isinstance(key, tuple) else (key,)))
                   | {"n": len(g), "start": g["t"].iloc[0], "end": g["t"].iloc[-1], "span_days": days[-1],
                      "ts_slope": ts, "ols_slope": b, "hac_se": se, "z": b / se if se > 0 else np.nan,
                      "crit": hac_crit(len(g), bw)})
    return pd.DataFrame(out)


def adjacent_steps(df: pd.DataFrame, unit_keys: list[str], k: int, col: str = "y") -> pd.DataFrame:
    """Return the level step between consecutive strata of each unit.

    The step is the median of the first k after minus the last k before. Strata with fewer
    than k values are skipped, so a step can cross more than one boundary.
    """
    out = []
    for key, g in df.dropna(subset=[col]).sort_values("run").groupby(unit_keys):
        segs = [s for _, s in sorted(g.groupby("stratum"), key=lambda kv: kv[1]["t"].iloc[0]) if len(s) >= k]
        for a, b in zip(segs, segs[1:]):
            out.append(dict(zip(unit_keys, key if isinstance(key, tuple) else (key,)))
                       | {"stratum_before": a["stratum"].iloc[0], "stratum_after": b["stratum"].iloc[0],
                          "t_before": a["t"].iloc[-1], "t_after": b["t"].iloc[0],
                          "delta": float(b[col].iloc[:k].median() - a[col].iloc[-k:].median())})
    return pd.DataFrame(out, columns=[*unit_keys, "stratum_before", "stratum_after", "t_before", "t_after", "delta"])


def placebo_steps(df: pd.DataFrame, keys: list[str], k: int, col: str = "y", gap: pd.Timedelta | None = None) -> pd.DataFrame:
    """Return window steps at every split inside each series (median of k after - k before).

    With gap, the after window starts at least gap later, to match a step across a data gap.
    """
    out = []
    g_ns = 0 if gap is None else int(pd.Timedelta(gap).value)
    for key, g in df.dropna(subset=[col]).sort_values("run").groupby(keys):
        y = g[col].to_numpy(dtype=float)
        n = len(y)
        if n < 2 * k:
            continue
        tn = pd.DatetimeIndex(g["t"]).as_unit("ns").asi8
        med = np.median(sliding_window_view(y, k), axis=1)  # med[s] = median(y[s:s + k])
        i = np.arange(k, n)  # split before y[i], before window y[i - k:i]
        j = np.maximum(i, np.searchsorted(tn, tn[i - 1] + g_ns, side="left")) if g_ns else i
        ok = j + k <= n
        if ok.any():
            out.append(pd.DataFrame({"t": g["t"].to_numpy()[j[ok]], "step": med[j[ok]] - med[i[ok] - k]})
                       .assign(**dict(zip(keys, key if isinstance(key, tuple) else (key,)))))
    return pd.concat(out, ignore_index=True)[[*keys, "t", "step"]] if out else pd.DataFrame(columns=[*keys, "t", "step"])


def change_points(df: pd.DataFrame, keys: list[str], col: str = "y", block_col: str = "block",
                  min_seg_col: str = "min_seg", crit: float | None = None,
                  scan_col: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Find change points per series (binary_segmentation, or scan_jumps where scan_col is True).

    Returns the change points (edge marks a split at the allowed limit) and the times
    eligible to start a segment.
    """
    cps, elig = [], []
    for key, g in df.dropna(subset=[col]).sort_values("run").groupby(keys):
        kv = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
        m, t, n = int(g[min_seg_col].iloc[0]), g["t"].to_numpy(), len(g)
        if n < 2 * m:
            continue
        scan = bool(g[scan_col].iloc[0]) if scan_col else False
        if scan:
            bs = scan_jumps(g[col].to_numpy(), m)
        else:
            bs = binary_segmentation(g[col].to_numpy(), m, int(g[block_col].iloc[0]), crit)
        if len(bs):
            split = bs["split"].to_numpy(dtype=int)
            cps.append(bs.assign(t=t[split], t_prev=t[split - 1], n=n, method="scan" if scan else "cusum",
                                 edge=(split == m) | (split == n - m), **kv))
        elig.append(pd.DataFrame({"t": t[m:n - m + 1]}).assign(**kv))
    cp_cols = [*keys, "t", "t_prev", "stat", "crit", "shift", "n", "method", "edge"]
    cp = pd.concat(cps, ignore_index=True)[cp_cols] if cps else pd.DataFrame(columns=cp_cols)
    return cp, (pd.concat(elig, ignore_index=True) if elig else pd.DataFrame(columns=[*keys, "t"]))
