import pandas as pd

from .. import periods

SEG_DATE = {0: "0 (start)"} | {i: f"{i} ({b.at:%Y-%m-%d})" for i, b in enumerate(periods.BREAKS, 1)}  # as B2, B3c, B4
RB_JUNE = pd.Timestamp("2026-06-01")  # RB stratum split (3a, B4)
RB_JUNE_TAG = " RB from 2026-06"

METRICS = ["T1", "T2", "fRB", "fCX", "fRO"]
FIDELITY = ("fRB", "fCX", "fRO")
RB_METRICS = ("fRB", "fCX")
FLOOR = {"fRB": 0.5, "fCX": 0.25, "fRO": 0.5}  # depolarising floors (B4 section 3)
NEAR_FLOOR = 0.02
SHORT_LIST_MAX = 14  # rb_lengths with at most this many lengths is the short list (B4 section 1)
QUALITY_SHORT = 92  # benchmarking.py: quality below this picks the short list
QUALITY_R2 = 0.8

MAIN_METHOD = {  # B4 main noise methods per DS3 metric, in order of preference (B4 section 4)
    "T1": ("fit_error_median",),
    "T2": ("fit_error_median",),
    "fRB": ("seed_error_median", "fit_error_median_x_sample_seed_ratio"),
    "fCX": ("seed_error_median", "fit_error_median_x_sample_seed_ratio"),
    "fRO": ("fit_error_median", "binomial_upper_median"),
}
DS3_LAG1_METHOD = "run_step_robust_sd_over_sqrt2"
DS1_1Q_FIT = ("final_step_fit_sd_median", "final_step_fit_sd_over_abs_new_median")  # 1q fit sd, high end (B3c)
DS1_1Q_MAIN = (  # 1q main noise in order of preference: centred scaled sd, else fit sd (B3c section 5)
    "final_step_fit_sd_median_x_rsd_zc", "final_step_fit_sd_over_abs_new_median_x_rsd_zc", *DS1_1Q_FIT)

MIN_N = 20
FINE_LADDER_05 = next(b.at for b in periods.BREAKS if b.what == "calibrate_1q code change")  # 0.5 us step (d2b)
FINE_LADDER_05_OFF: periods.Period = tuple(  # old calibrate_1q code back, no 0.5 us step (d2b)
    next(b.at for b in periods.BREAKS if b.what == w) for w in ("calibrate_1q old code back", "calibrate_1q new code back"))
SIGNAL_MIN = 2.0  # signal ratio at or above which a series shows signal (01_eda Approach)
MAX_GAP_PER_LAG = pd.Timedelta(hours=36)  # pairs at most lag x 36 h apart (B4 section 4)
NEAR_EVENT = pd.Timedelta("2D")
KOLMOGOROV_95 = 1.358  # 95% quantile of the supremum of a Brownian bridge

F01_BIG = 100.0  # kHz: a change at or above this is a large f01 move (d2a section 2)
F01_SMALL = 30.0  # kHz: a change below this is small (d2a section 2)
F01_REVERT = 0.5  # two changes undo each other when |sum| < this x the larger (d2a section 1)
F01_GAP_MAX = pd.Timedelta("7D")  # a longer gap between a qubit's runs starts a new segment (d2a section 1)

DRIVE_SCALE_TEMPLATE = "model.pulse_channels{Ch*.Q*.drive}.pulse_channel.scale"
