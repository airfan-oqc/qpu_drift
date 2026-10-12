import numpy as np
import pandas as pd

SCALES = np.array([1.0, 10.0, 100.0])  # jump and outlier sds as multiples of J or O
LAM = tuple(f"lam{k}" for k in range(len(SCALES)))
RK = tuple(f"r{k}" for k in range(len(SCALES)))
BASE = ("R", "q")
STAGE = ("s1", "s2")
REVERT = ("tau", "w")
MODELS = {
    "M0": (),
    "M1": (*LAM, "J"),
    "M2": (*RK, "O"),
    "M3": (*LAM, "J", *RK, "O"),
    "M3s": (*LAM, "J", *RK, "O", *STAGE),
    "M4": (*LAM, "J", *RK, "O", *REVERT),
    "M4s": (*LAM, "J", *RK, "O", *STAGE, *REVERT),
}
STAGES = ("full ladder", "1 step short", "2+ short")  # FineFrequency stage codes 0, 1, 2
BOUNDS = {"R": (np.log(0.05), np.log(1e4)), "q": (np.log(0.01), np.log(1e4)), "J": (np.log(1.0), np.log(1e5)),
          "O": (np.log(1.0), np.log(1e5)), "s1": (np.log(0.2), np.log(200.0)), "s2": (np.log(0.2), np.log(200.0)),
          "tau": (np.log(0.5), np.log(2000.0)), "w": (-9.0, 9.0)}
BOUNDS |= {n: (np.log(1e-5), np.log(1.0)) for n in LAM}  # per day
BOUNDS |= {n: (-11.5, -1.4) for n in RK}  # logit of 1e-5 to 0.2 per run
CYCLES, GRID, SPAN0 = 8, 9, 2.0
LOG2PI = np.log(2 * np.pi)


def natural(th: dict) -> dict:
    """Convert fitted (log, logit) parameters to natural units.

    R in kHz^2 per run, q and rates per day, probabilities per run, J and O in kHz, tau in days.
    """
    out = {"R": np.exp(2 * th["R"]), "q": np.exp(2 * th["q"])}
    if "J" in th:
        out |= {n: np.exp(th[n]) for n in LAM} | {"J": np.exp(th["J"])}
    if "O" in th:
        out |= {n: 1 / (1 + np.exp(-th[n])) for n in RK} | {"O": np.exp(th["O"])}
    if "s1" in th:
        out |= {"m1": np.exp(2 * th["s1"]), "m2": np.exp(2 * th["s2"])}
    if "tau" in th:
        out |= {"tau": np.exp(th["tau"]), "w": 1 / (1 + np.exp(-th["w"]))}
    return out


def panel(df: pd.DataFrame, score) -> dict:
    """Build padded qubits x rows arrays from rows with qubit, seg, t, y (kHz) and optional stage.

    score marks the rows whose predictive density counts; a segment's first row never counts.
    """
    qs = sorted(df["qubit"].unique())
    L = df.groupby("qubit").size().max()
    shape = (len(qs), L)
    out = {"qubits": qs, "Y": np.zeros(shape), "DT": np.zeros(shape), "START": np.zeros(shape, bool),
           "VALID": np.zeros(shape, bool), "SCORE": np.zeros(shape, bool), "STAGE": np.zeros(shape, int),
           "index": np.full(shape, -1)}
    score = np.asarray(score)
    for i, q in enumerate(qs):
        g = df[df["qubit"] == q]
        pos = df.index.get_indexer(g.index)
        n = len(g)
        start = (g["seg"] != g["seg"].shift()).to_numpy()
        out["Y"][i, :n] = g["y"].to_numpy()
        out["DT"][i, :n] = np.where(start, 0.0, g["t"].diff().dt.total_seconds().fillna(0).to_numpy() / 86400)
        out["START"][i, :n] = start
        out["VALID"][i, :n] = True
        out["SCORE"][i, :n] = ~start & score[pos]
        if "stage" in g:
            out["STAGE"][i, :n] = g["stage"].to_numpy()
        out["index"][i, :n] = g.index
    return out


def _components(nat: dict, dt):
    """Return prior log weights and extra variances of the jump and outlier components at one run.

    At most one jump per interval; its scale k is picked in proportion to lam_k.
    """
    zero = np.zeros_like(nat["R"] * dt)
    js, os_ = [(zero, zero)], [(zero, zero)]
    with np.errstate(divide="ignore"):
        if "J" in nat:
            tot = sum(nat[n] for n in LAM)
            pj = -np.expm1(-tot * dt)
            js = [(np.log1p(-pj), zero)] + [(np.log(pj * nat[n] / tot), (s * nat["J"]) ** 2 + zero)
                                            for n, s in zip(LAM, SCALES)]
        if "O" in nat:
            r = sum(nat[n] for n in RK)
            os_ = [(np.log1p(-r) + zero, zero)] + [(np.log(nat[n]) + zero, (s * nat["O"]) ** 2 + zero)
                                                  for n, s in zip(RK, SCALES)]
    return js, os_


def erf(x):
    """Return erf(x) via Abramowitz and Stegun 7.1.26 (abs error below 1.5e-7)."""
    s = np.sign(x)
    x = np.abs(x)
    t = 1 / (1 + 0.3275911 * x)
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return s * y


def run_filter(th: dict, data: dict, detail: bool = False, lag1: bool = False):
    """Run an approximate (GPB1) mixture Kalman filter over all qubits and candidates.

    Returns the log-likelihood per qubit and candidate; with detail, also per-row logpred,
    pit, p_jump, p_out, level and R_t. With lag1 as well, p_jump_s and p_out_s are the jump and
    outlier probabilities given the next run too (the filtered ones where no next run follows).
    """
    nat = natural(th)
    R, q = nat["R"], nat["q"]
    one = np.ones_like(R)
    m1, m2 = nat.get("m1", one), nat.get("m2", one)
    w = nat.get("w", 0 * one)
    tau = nat.get("tau")
    Pst = 0 * one  # stationary variance of x; a segment starts with x from it and b unknown
    if tau is not None and "J" in nat:
        Pst = w * w * tau / 2 * sum(nat[n] * (s * nat["J"]) ** 2 for n, s in zip(LAM, SCALES))
    Y, DT, START, VALID, SCORE, STG = (data[k] for k in ["Y", "DT", "START", "VALID", "SCORE", "STAGE"])
    mb, mx, Pbb, Pbx, Pxx, ll = (np.zeros_like(R) for _ in range(6))
    if detail:
        keys = ["logpred", "pit", "p_jump", "p_out", "level", "R_t"] + (["p_jump_s", "p_out_s"] if lag1 else [])
        rec = {k: np.full(Y.shape, np.nan) for k in keys}
        prev = None
    phi = lambda z: 0.5 * (1 + erf(z / np.sqrt(2)))
    for k in range(Y.shape[1]):
        y, dt, st, ok, sg = Y[:, k, None], DT[:, k, None], START[:, k, None], VALID[:, k, None], STG[:, k, None]
        Rt = R * np.where(sg == 1, m1, np.where(sg == 2, m2, 1.0))
        a = np.exp(-dt / tau) if tau is not None else one
        Pbb_p, Pbx_p, Pxx_p, mx_p = Pbb + q * dt, a * Pbx, a * a * Pxx, a * mx
        v = y - mb - mx_p
        js, os_ = _components(nat, dt)
        lw, mbs, mxs, cbb, cbx, cxx, isj, iso, S_all, prior = ([] for _ in range(10))
        with np.errstate(divide="ignore", invalid="ignore"):
            for i, (lwj, vj) in enumerate(js):
                bb, bx, xx = Pbb_p + vj * (1 - w) ** 2, Pbx_p + vj * w * (1 - w), Pxx_p + vj * w * w
                tot = bb + 2 * bx + xx
                for j, (lwo, vo) in enumerate(os_):
                    S = tot + Rt + vo
                    kb, kx = (bb + bx) / S, (bx + xx) / S
                    lw.append(lwj + lwo - 0.5 * (LOG2PI + np.log(S) + v * v / S))
                    mbs.append(mb + kb * v)
                    mxs.append(mx_p + kx * v)
                    cbb.append(bb - kb * kb * S)
                    cbx.append(bx - kb * kx * S)
                    cxx.append(xx - kx * kx * S)
                    isj.append(i > 0)
                    iso.append(j > 0)
                    S_all.append(S)
                    prior.append(np.exp(lwj + lwo))
        lw = np.array(lw)
        top = lw.max(axis=0)
        lse = top + np.log(np.exp(lw - top).sum(axis=0))
        pi = np.exp(lw - lse)
        mbs, mxs = np.array(mbs), np.array(mxs)
        mbn, mxn = (pi * mbs).sum(axis=0), (pi * mxs).sum(axis=0)
        db, dx = mbs - mbn, mxs - mxn
        Pbbn = (pi * (np.array(cbb) + db * db)).sum(axis=0)
        Pbxn = (pi * (np.array(cbx) + db * dx)).sum(axis=0)
        Pxxn = (pi * (np.array(cxx) + dx * dx)).sum(axis=0)
        use = ok & ~st
        ll += np.where(SCORE[:, k, None], lse, 0.0)
        mb = np.where(st, y, np.where(ok, mbn, mb))
        mx = np.where(st, 0.0, np.where(ok, mxn, mx))
        Pbb = np.where(st, Rt + Pst, np.where(ok, Pbbn, Pbb))
        Pbx = np.where(st, -Pst, np.where(ok, Pbxn, Pbx))
        Pxx = np.where(st, Pst, np.where(ok, Pxxn, Pxx))
        if detail:
            u = use[:, 0]
            isj_, iso_ = np.array(isj)[:, None, None], np.array(iso)[:, None, None]
            rec["logpred"][u, k] = lse[u, 0]
            rec["pit"][u, k] = sum(p * phi(v / np.sqrt(S)) for p, S in zip(prior, S_all))[u, 0]
            rec["p_jump"][u, k] = (pi * isj_).sum(axis=0)[u, 0]
            rec["p_out"][u, k] = (pi * iso_).sum(axis=0)[u, 0]
            o = ok[:, 0]
            rec["level"][o, k] = (mb + mx)[o, 0]
            rec["R_t"][o, k] = Rt[o, 0]
            if lag1:
                if prev is not None:
                    _lag1(rec, prev, k, y, dt, a, q, Rt, js, os_, use, isj_, iso_)
                prev = (pi, mbs, mxs, np.array(cbb), np.array(cbx), np.array(cxx), use)
    if not detail:
        return ll
    if lag1:  # a row with no next run in its segment keeps its filtered probabilities
        last = np.ones_like(START)
        last[:, :-1] = START[:, 1:] | ~VALID[:, 1:]
        for s, f in [("p_jump_s", "p_jump"), ("p_out_s", "p_out")]:
            fill = last & np.isfinite(rec[f]) & ~np.isfinite(rec[s])
            rec[s][fill] = rec[f][fill]
    return ll[:, 0], rec


def _lag1(rec, prev, k, y, dt, a, q, Rt, js, os_, use, isj_, iso_):
    """Reweight row k-1's components by row k's predictive density under each of them.

    Writes P(jump) and P(outlier) at row k-1 given rows up to k into rec p_jump_s and p_out_s.
    """
    ppi, pmb, pmx, pbb, pbx, pxx, puse = prev
    lp = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for c in range(len(ppi)):
            bb0, bx0, xx0 = pbb[c] + q * dt, a * pbx[c], a * a * pxx[c]
            v = y - pmb[c] - a * pmx[c]
            terms = []
            for lwj, vj in js:
                for lwo, vo in os_:
                    S = bb0 + 2 * bx0 + xx0 + vj + Rt + vo  # the split jump adds vj to b + x in full
                    terms.append(lwj + lwo - 0.5 * (LOG2PI + np.log(S) + v * v / S))
            terms = np.array(terms)
            top = terms.max(axis=0)
            lp.append(top + np.log(np.exp(terms - top).sum(axis=0)))
        lp = np.log(ppi) + np.array(lp)
    lp = np.exp(lp - lp.max(axis=0))
    lp /= lp.sum(axis=0)
    link = (puse & use)[:, 0]
    rec["p_jump_s"][link, k - 1] = (lp * isj_).sum(axis=0)[link, 0]
    rec["p_out_s"][link, k - 1] = (lp * iso_).sum(axis=0)[link, 0]


def fit(data: dict, model: str, start: dict, cycles: int = CYCLES, span0: float = SPAN0) -> tuple[dict, np.ndarray]:
    """Fit a model per qubit by coordinate grid search; return parameters and log-likelihood.

    Each sweep tries GRID values of one parameter, halving its span when the best is inside.
    """
    names = BASE + MODELS[model]
    th = {n: np.clip(np.asarray(start[n], dtype=float).copy(), *BOUNDS[n]) for n in names}
    span = {n: np.full(len(th["R"]), span0) for n in names}
    off = np.linspace(-1, 1, GRID)
    rows = np.arange(len(th["R"]))
    for _ in range(cycles):
        for n in names:
            cand = np.clip(th[n][:, None] + span[n][:, None] * off, *BOUNDS[n])
            full = {k: (cand if k == n else np.repeat(th[k][:, None], GRID, axis=1)) for k in names}
            ll = run_filter(full, data)
            best = np.nanargmax(np.where(np.isfinite(ll), ll, -np.inf), axis=1)
            th[n] = cand[rows, best]
            inner = (best > 0) & (best < GRID - 1)
            span[n] = np.where(inner, span[n] / 2, span[n])
    ll = run_filter({k: v[:, None] for k, v in th.items()}, data)[:, 0]
    return th, ll


def _jump(nat: dict, dt, shape, rng) -> np.ndarray:
    """Draw the jump in each interval (0 when none), at most one per interval as in the filter."""
    if "J" not in nat:
        return np.zeros(shape)
    lam = np.stack([nat[n] for n in LAM], axis=-1)
    tot = lam.sum(axis=-1)
    hit = rng.random(shape) < -np.expm1(-tot * dt)
    cum = np.cumsum(lam / tot[..., None], axis=-1)
    k = (rng.random(shape)[..., None] >= cum).sum(axis=-1).clip(max=len(SCALES) - 1)
    return hit * rng.standard_normal(shape) * SCALES[k] * nat["J"]


def simulate(nat: dict, data: dict, n_rep: int, rng: np.random.Generator) -> np.ndarray:
    """Simulate y from a fitted model on the panel's own times, segments and stages.

    Returns (qubits, n_rep, rows). At segment starts the baseline is 0 and x comes from an
    independent copy burned in for 5 tau, as the filter assumes.
    """
    Qn, L = data["Y"].shape
    nat = {k: np.asarray(v, dtype=float)[:, None] for k, v in nat.items()}
    one = np.ones((Qn, 1))
    m1, m2 = nat.get("m1", one), nat.get("m2", one)
    w = nat.get("w", 0 * one)
    tau = nat.get("tau")
    shape = (Qn, n_rep)
    b, x = np.zeros(shape), np.zeros(shape)
    x_burn = np.zeros(shape)
    if tau is not None and "J" in nat:  # 50 steps of tau / 10, with Poisson jump counts per step
        h = tau / 10
        ah = np.exp(-h / tau)
        c = (1 - ah * ah) * tau / (2 * h)  # mean squared decay of a jump inside a step
        for _ in range(50):
            v = sum(rng.poisson(nat[n] * h, shape) * (s * nat["J"]) ** 2 for n, s in zip(LAM, SCALES))
            x_burn = ah * x_burn + w * np.sqrt(c * v) * rng.standard_normal(shape)
    out = np.full((Qn, n_rep, L), np.nan)
    for k in range(L):
        dt, st, ok, sg = (data[n][:, k, None] for n in ["DT", "START", "VALID", "STAGE"])
        jmp = _jump(nat, dt, shape, rng)
        a = np.exp(-dt / tau) if tau is not None else one
        b = np.where(st, 0.0, b + np.sqrt(nat["q"] * dt) * rng.standard_normal(shape) + (1 - w) * jmp)
        x = np.where(st, x_burn, a * x + w * jmp)
        if tau is not None:  # independent stationary copy of x, read at segment starts
            x_burn = a * x_burn + w * _jump(nat, dt, shape, rng)
        Rt = nat["R"] * np.where(sg == 1, m1, np.where(sg == 2, m2, 1.0))
        e = np.sqrt(Rt) * rng.standard_normal(shape)
        if "O" in nat:
            p = np.stack([nat[n][:, 0] for n in RK], axis=1)
            cum = np.cumsum(p, axis=1)[:, None, :]
            kk = (rng.random(shape)[..., None] >= cum).sum(axis=-1)  # 0..2 outlier scale, 3 none
            e = e + rng.standard_normal(shape) * np.append(SCALES, 0.0)[kk] * nat["O"]
        out[:, :, k] = np.where(ok, b + x + e, np.nan)
    return out
