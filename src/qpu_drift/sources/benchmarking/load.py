from pathlib import Path

import pandas as pd

from ...paths import CLEAN, DERIVED

CLEAN_DIR = CLEAN / "benchmarking"
DERIVED_DIR = DERIVED / "benchmarking"
RUNS_PATH = CLEAN_DIR / "runs.parquet"
KPI_PATH = CLEAN_DIR / "kpi.parquet"
RUNS_L2_PATH = CLEAN_DIR / "runs_enriched.parquet"
KPI_L2_PATH = CLEAN_DIR / "kpi_enriched.parquet"
KPI_SUPPLEMENTED_PATH = DERIVED_DIR / "kpi_supplemented.parquet"  # supplement-kpi output


def load_runs(path: Path = RUNS_PATH) -> pd.DataFrame:
    """Load the L1 run table: created_at, topology, has_openpulse (one row per run)."""
    return pd.read_parquet(path)


def load_kpi(
    path: Path = KPI_PATH,
    metric: str | None = None,
    element: str | None = None,
) -> pd.DataFrame:
    """Load the L1 KPI table, optionally sliced by metric or element.

    A null value is a gated reject; use load_kpi_l2 for provenance and recovered rejects.
    """
    kpi = pd.read_parquet(path)
    if metric is not None:
        kpi = kpi[kpi["metric"] == metric]
    if element is not None:
        kpi = kpi[kpi["element"] == element]
    return kpi.reset_index(drop=True)


def load_runs_l2(path: Path = RUNS_L2_PATH) -> pd.DataFrame:
    """Load the L2 run table: the L1 columns plus gating_regime."""
    return pd.read_parquet(path)


def load_kpi_l2(
    path: Path = KPI_L2_PATH,
    metric: str | None = None,
    element: str | None = None,
    value_status: str | None = None,
    value_source: str | None = None,
) -> pd.DataFrame:
    """Load the L2 KPI table, optionally sliced by metric, element, status or source.

    ds1 cells read NaN until decode-ds1 fills them (see datasheet 3).
    Pass path=KPI_SUPPLEMENTED_PATH for the supplement-kpi copy.
    """
    kpi = pd.read_parquet(path)
    if metric is not None:
        kpi = kpi[kpi["metric"] == metric]
    if element is not None:
        kpi = kpi[kpi["element"] == element]
    if value_status is not None:
        kpi = kpi[kpi["value_status"] == value_status]
    if value_source is not None:
        kpi = kpi[kpi["value_source"] == value_source]
    return kpi.reset_index(drop=True)
