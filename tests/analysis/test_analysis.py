import numpy as np
import pandas as pd

from qpu_drift.analysis import jumps, methods, series
from qpu_drift.analysis.constants import FINE_LADDER_05_OFF


def test_rsd_of_normal():
    rng = np.random.default_rng(1)
    assert abs(methods.rsd(rng.normal(0, 2, 20000)) - 2) < 0.05


def test_theil_sen_line_with_outlier():
    x = np.arange(20.0)
    y = 3 * x + 1
    y[5] = 500
    slope, icpt = methods.theil_sen(x, y)
    assert abs(slope - 3) < 1e-9 and abs(icpt - 1) < 1e-9


def test_window_steps():
    y = np.r_[np.zeros(10), np.ones(10)]
    st = methods.window_steps(y, 5)
    assert len(st) == 21 and np.isnan(st[4]) and st[10] == 1 and st[5] == 0 and np.isnan(st[16])


def test_binary_segmentation_finds_step_and_not_noise():
    rng = np.random.default_rng(2)
    y = np.r_[rng.normal(0, 1, 150), rng.normal(2, 1, 150)]
    bs = methods.binary_segmentation(y, 10, 1)
    assert len(bs) >= 1 and abs(bs.loc[bs["stat"].idxmax(), "split"] - 150) <= 5
    hits = sum(len(methods.binary_segmentation(np.random.default_rng(s).normal(0, 1, 300), 10, 1)) > 0
               for s in range(100))
    assert hits <= 12  # nominal 5% false alarm at the top level


def test_ols_slope_hac():
    rng = np.random.default_rng(3)
    x = np.arange(200.0)
    b, se = methods.ols_slope_hac(x, 0.01 * x + rng.normal(0, 1, 200), 5)
    assert abs(b - 0.01) < 4 * se and se > 0


def test_fit_timescale_recovers_tau():
    lag = np.array([1, 2, 3, 4, 7, 14, 28, 56])
    fit = methods.fit_timescale(lag, 1 + 3 * (1 - np.exp(-lag / 5)))
    assert abs(fit["tau"] - 5) / 5 < 0.05 and abs(fit["c"] - 1) < 0.05 and not fit["tau_at_edge"]
    assert methods.fit_timescale(lag, 1 + 0.2 * lag)["rw_sse"] < 1e-12


def test_block_length():
    lags = [1, 2, 3, 4, 7, 14, 28]
    assert methods.block_length(pd.Series([1, 1.5, 1.8, 2, 2.2, 2.25, 2.3], index=lags)) == 7  # 2.2 >= 2.3 / 1.1
    assert methods.block_length(pd.Series([1, 2, 3, 4, 5, 6, 8], index=lags)) == 28


def test_lag_pairs_gap_and_cuts():
    t = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-05", "2025-01-06"])
    df = pd.DataFrame({"k": "a", "run": [0, 1, 2, 3], "t": t, "y": [0.0, 1.0, 3.0, 6.0]})
    p = methods.lag_pairs(df, ["k"], 1)
    assert list(p["d"]) == [1.0, 3.0]  # 2025-01-02 -> 01-05 is over 36 h
    p = methods.lag_pairs(df, ["k"], 1, cuts=[pd.Timestamp("2025-01-01 12:00")])
    assert list(p["d"]) == [3.0]


def test_near():
    at = pd.to_datetime(["2025-01-10"])
    t = pd.to_datetime(["2025-01-07", "2025-01-08", "2025-01-12", "2025-01-13"])
    assert list(methods.near(t, at, pd.Timedelta("2D"))) == [False, True, True, False]


def test_adjacent_steps_and_change_points():
    n = 60
    df = pd.DataFrame({"u": "a", "run": np.arange(n), "t": pd.date_range("2025-01-01", periods=n),
                       "stratum": np.where(np.arange(n) < 30, "s1", "s2"),
                       "y": np.where(np.arange(n) < 30, 0.0, 1.0) + np.random.default_rng(4).normal(0, 0.1, n),
                       "block": 1, "min_seg": 7})
    st = methods.adjacent_steps(df, ["u"], 7)
    assert len(st) == 1 and abs(st["delta"].iloc[0] - 1) < 0.2
    cp, elig = methods.change_points(df, ["u"])
    assert len(cp) >= 1 and (cp["t"] == pd.Timestamp("2025-01-31")).any() and len(elig) == n - 14 + 1


def _toy_panel(n=40, seed=0):
    rng = np.random.default_rng(seed)
    t = pd.Timestamp("2025-01-01") + pd.to_timedelta(np.arange(n) * 0.5 + rng.random(n) * 0.1, unit="D")
    df = pd.DataFrame({"qubit": 1, "seg": np.where(np.arange(n) < n // 2, "a", "b"), "t": t,
                       "y": np.cumsum(rng.normal(0, 3, n)), "stage": rng.integers(0, 3, n)})
    return df, jumps.panel(df, np.ones(n, bool))


def _th(**kw):
    return {k: np.array([[v]]) for k, v in kw.items()}


def test_jump_filter_m0_is_kalman():
    df, data = _toy_panel()
    R, q = 4.0, 2.0
    ll = jumps.run_filter(_th(R=np.log(R) / 2, q=np.log(q) / 2), data)[0, 0]
    exp, b, P = 0.0, 0.0, 0.0
    for k in range(len(df)):
        y, dt, st = data["Y"][0, k], data["DT"][0, k], data["START"][0, k]
        if st:
            b, P = y, R
            continue
        P += q * dt
        S = P + R
        v = y - b
        exp += -0.5 * (np.log(2 * np.pi * S) + v * v / S)
        b, P = b + P / S * v, P * R / S
    assert abs(ll - exp) < 1e-9


def test_jump_filter_nesting():
    _, data = _toy_panel()
    m3 = dict(R=0.5, q=0.3, lam0=-4.0, lam1=-5.0, lam2=-7.0, J=2.0, r0=-4.0, r1=-6.0, r2=-8.0, O=2.5)
    ll3 = jumps.run_filter(_th(**m3), data)[0, 0]
    assert abs(jumps.run_filter(_th(**m3, s1=0.0, s2=0.0), data)[0, 0] - ll3) < 1e-9
    assert abs(jumps.run_filter(_th(**m3, tau=np.log(5.0), w=-60.0), data)[0, 0] - ll3) < 1e-9
    assert jumps.run_filter(_th(**m3, s1=1.0, s2=2.0), data)[0, 0] != ll3


def test_jump_filter_lag1_labels():
    n = 40
    t = pd.Timestamp("2025-01-01") + pd.to_timedelta(np.arange(n) * 0.5, unit="D")
    y = np.random.default_rng(5).normal(0, 1, n)
    y[10] += 100  # one bad run: the next run goes back
    y[25:] += 100  # a level jump: the next runs stay
    df = pd.DataFrame({"qubit": 1, "seg": "a", "t": t, "y": y})
    data = jumps.panel(df, np.ones(n, bool))
    m3 = dict(R=0.0, q=np.log(0.1) / 2, lam0=np.log(0.05), lam1=np.log(0.05), lam2=np.log(0.05),
              J=np.log(10.0), r0=-3.9, r1=-3.9, r2=-3.9, O=np.log(10.0))
    for extra in [{}, dict(s1=0.0, s2=0.0, tau=np.log(5.0), w=-60.0)]:
        ll, rec = jumps.run_filter(_th(**m3, **extra), data, detail=True, lag1=True)
        assert rec["p_out_s"][0, 10] > 0.9 and rec["p_jump_s"][0, 10] < 0.1
        assert rec["p_jump_s"][0, 25] > 0.9 and rec["p_out_s"][0, 25] < 0.1
        assert np.isnan(rec["p_jump_s"][0, 0]) and rec["p_jump_s"][0, -1] == rec["p_jump"][0, -1]
        assert ll == jumps.run_filter(_th(**m3, **extra), data)[0, 0]


def test_jump_simulate_noise():
    _, data = _toy_panel(n=200)
    nat = {"R": np.array([4.0]), "q": np.array([1e-8])}
    sim = jumps.simulate(nat, data, 200, np.random.default_rng(1))
    assert sim.shape == (1, 200, 200) and not np.isnan(sim).any()
    d = np.diff(sim[0], axis=1)[:, ~data["START"][0, 1:]]
    assert abs(d.var() / 8.0 - 1) < 0.05


def test_jump_filter_m4_starts_x_stationary():
    t = pd.Timestamp("2025-01-01") + pd.to_timedelta([0.0, 0.5], unit="D")
    df = pd.DataFrame({"qubit": 1, "seg": "a", "t": t, "y": [0.0, 30.0]})
    data = jumps.panel(df, np.ones(2, bool))
    R, q, lam, J, tau, w = 4.0, 1e-8, 1e-3, 1e4, 0.5, 0.5
    th = _th(R=np.log(R) / 2, q=np.log(q) / 2, lam0=np.log(lam), lam1=-60.0, lam2=-60.0, J=np.log(J),
             r0=-60.0, r1=-60.0, r2=-60.0, O=0.0, tau=np.log(tau), w=0.0)
    Pst = w * w * tau / 2 * lam * J * J
    S0 = 2 * R + q * 0.5 + Pst * (1 - np.exp(-0.5 / tau)) ** 2
    pj = -np.expm1(-lam * 0.5)
    dens = lambda S: np.exp(-0.5 * 30.0**2 / S) / np.sqrt(2 * np.pi * S)
    exp = np.log((1 - pj) * dens(S0) + pj * dens(S0 + J * J))
    assert abs(jumps.run_filter(th, data)[0, 0] - exp) < 1e-9


def test_jump_simulate_x_stationary_at_start():
    df = pd.DataFrame({"qubit": 1, "seg": ["a"], "t": [pd.Timestamp("2025-01-01")], "y": [0.0]})
    data = jumps.panel(df, np.ones(1, bool))
    nat = {"R": np.array([1e-8]), "q": np.array([1e-8]), "lam0": np.array([2.0]), "lam1": np.array([0.01]),
           "lam2": np.array([0.0]), "J": np.array([1.0]), "tau": np.array([5.0]), "w": np.array([0.5])}
    sim = jumps.simulate(nat, data, 40000, np.random.default_rng(2))
    Pst = 0.25 * 5.0 / 2 * (2.0 + 0.01 * 100.0)
    assert abs(sim[0, :, 0].var() / Pst - 1) < 0.05


def test_f01_moves_kinds_ctrl_and_gap():
    t = pd.Timestamp("2025-01-01") + pd.to_timedelta([0.5 * i for i in range(8)] + [14.0], unit="D")
    y = [0, 0, 0, 0, 0, 200, 0, 0, 300]
    warned = [False] * 9
    warned[4] = True
    rows = pd.DataFrame({"qubit": 1, "seg": "a", "t": t, "y_hz": np.array(y) * 1e3, "warned": warned})
    f = series.f01_moves(rows)
    assert list(f["kind"]) == ["", "", "", "", "", "out", "back", "", ""]
    assert list(f["ctrl"]) == [False, False, False, True, True, True, True, False, False]
    assert list(f["quiet"]) == [False, False, True, False, False, False, False, False, False]
    assert f["seg"].iloc[8] != f["seg"].iloc[7] and np.isnan(f["d"].iloc[8])


def test_fine_freq_stage_ladder_by_date():
    t_old, t_off, t_new = pd.Timestamp("2025-01-10"), FINE_LADDER_05_OFF[0] + pd.Timedelta("1D"), pd.Timestamp("2025-06-01")
    rows = [(t, "Q1", s) for t in (t_old, t_off, t_new) for s in ("ramsey", "freq", "freq")]
    rows += [(t_new, "Q1", "freq"), (t_new, "Q2", "ramsey")]
    res = pd.DataFrame(rows, columns=["start", "key", "step"]).assign(routine="calibrate_1q")
    out = series.fine_freq_stage(res).set_index(["start", "qubit"])
    assert out.loc[(t_old, 1), "stage"] == 0 and out.loc[(t_off, 1), "stage"] == 0
    assert out.loc[(t_new, 1), "n_freq"] == 3 and out.loc[(t_new, 1), "stage"] == 0
    assert out.loc[(t_new, 2), "stage"] == 2


def test_ds1_1q_noise_main_and_fit():
    rows = [("1q.freq", "final_step_fit_sd_median", 1.0), ("1q.freq", "final_step_fit_sd_median_x_rsd_zc", 1.5),
            ("1q.amp_x_scale", "final_step_fit_sd_over_abs_new_median", 0.1),
            ("1q.amp_x_scale", "final_step_fit_sd_over_abs_new_median_x_rsd_zc", 0.12),
            ("1q.freq_shift_amp", "final_step_fit_sd_median", 2.0),
            ("1q.freq_shift_amp", "payload_step_robust_sd_over_sqrt2", 3.0)]
    noise = pd.DataFrame(rows, columns=["param", "method", "sigma"]).assign(
        source="ds1_1q", entity="4", regime="2 (2024-09-18)", n=20)
    out = series.ds1_1q_noise(noise).droplevel(["qubit", "stratum"])
    assert out.loc["freq", "sigma"] == 1.0 and out.loc["freq", "sigma_main"] == 1.5
    assert out.loc["amp_x_scale", "sigma"] == 0.1 and out.loc["amp_x_scale", "sigma_main"] == 0.12
    assert out.loc["freq_shift_amp", "sigma_main"] == 2.0
    assert out.loc["freq_shift_amp", "main_method"] == "final_step_fit_sd_median"
