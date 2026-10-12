import re

import pandas as pd

METRICS_1Q = ["T1", "T2", "fRB", "fRO"]  # per qubit; fRO carries no _error/_r2
METRIC_2Q = "fCX"                        # per coupling

RUN_COLS_L1 = ["created_at", "topology", "has_openpulse"]
RUN_COLS_L2 = [*RUN_COLS_L1, "gating_regime"]  # L2 stamps the producer era

KPI_COLS_L1 = [
    "created_at", "element", "element_type", "metric", "value", "error", "r2",
]
KPI_COLS_L2 = [
    *KPI_COLS_L1, "value_status", "value_source", "result_id", "measured_at",
]

R2_GATE = 0.9  # post_process_benchmark nulls a fit below this (datasheet 3)

PHYS_RANGE = {  # physical band, lower bound exclusive
    "T1": (0.0, 150e-6),
    "T2": (0.0, 200e-6),
    "fRB": (0.0, 1.0),
    "fCX": (0.0, 1.0),
    "fRO": (0.0, 1.0),  # point value with no R2 in any era, so range-gated only
}

VALUE_STATUSES = {  # how a cell was settled; unresolved = not yet settled by output_info
    "ok", "out_of_range", "low_r2", "no_fit", "no_result", "unresolved",
    "no_r2",  # in-range fit value that nothing ever R2-checked
}

VALUE_SOURCES = {  # which store supplied the coalesced value
    "qcaas",        # served payload value (tier 1, unconditional)
    "output_info",  # pre-gate benchmarks value (tier 2, unconditional)
    "ds1",          # era-3 reject refilled on demand from raw DS1 (tier 3)
    "none",         # no store held a value, so the value is NaN
}

FILTER = pd.Timestamp("2024-09-10")   # era0_ungated -> era1_filter boundary (producer d556bc2)
UNGATED = pd.Timestamp("2025-07-07")  # era1_filter -> era2_ungated boundary
GATE = pd.Timestamp("2025-08-22")     # era2_ungated -> era3_postproc boundary
R2_CRASH = pd.Timestamp("2025-04-11")  # maac 1.5.3 broke T1/T2 R2 until UNGATED (datasheet 3)
R2_CRASH_METRICS = {"T1", "T2"}
RUN_MATCH_TOLERANCE = pd.Timedelta(hours=8)  # own output_info file; not the 24h id guard (datasheet 3)

_OI_GROUPS = {  # metric -> (result_ids group, fit_quality group) in an output_info file
    "T1": ("t1", "t1s"),
    "T2": ("t2e", "t2s"),
    "fRB": ("rb1q", "fidelities_1q"),
    "fRO": ("zmap", "fidelities_ro"),
    "fCX": ("rb2q", "fidelities_2q"),
}
_ELEMENT_KEYED = {"fRB", "fCX"}  # metrics whose output_info result_id is per element

METRIC_MEASUREMENT_TYPE = {  # KPI metric -> DS1 measurement_type that produces it
    "T1": "T1",
    "T2": "T2echo",
    "fRB": "RandomizedBenchmarking1q",
    "fCX": "RandomizedBenchmarking2q",
    "fRO": "ZMap",
}
MEASUREMENT_TYPE_METRIC = {v: k for k, v in METRIC_MEASUREMENT_TYPE.items()}

_NO_FIT_RE = re.compile(r"did not return a (.+?) for\s+(\S+)", re.IGNORECASE)  # no_fit vs no_result
_NO_FIT_PHRASE = {  # warning phrase -> metric it announces a missing fit for
    "2q gate fidelity": METRIC_2Q,
    "1q gate fidelity": "fRB",
    "t1": "T1",
    "t2": "T2",
    "readout fidelity": "fRO",
}
