from pathlib import Path

import pandas as pd

from ...paths import DERIVED

DERIVED_DIR = DERIVED / "run_map"
RUNS_PATH = DERIVED_DIR / "runs.parquet"
ENTITIES_PATH = DERIVED_DIR / "run_entities.parquet"
RESULTS_PATH = DERIVED_DIR / "run_results.parquet"
ROWS_PATH = DERIVED_DIR / "run_rows.parquet"


def load_run_map(path: Path = RUNS_PATH, routine: str | None = None) -> pd.DataFrame:
    """Load the run table (one row per output_info file), optionally for one routine."""
    runs = pd.read_parquet(path)
    if routine is not None:
        runs = runs[runs["routine"] == routine].reset_index(drop=True)
    return runs


def load_run_entities(
    path: Path = ENTITIES_PATH,
    routine: str | None = None,
    param: str | None = None,
) -> pd.DataFrame:
    """Load the per-run entity table (one row per run, entity and param), optionally sliced."""
    filters = [(c, "==", v) for c, v in (("routine", routine), ("param", param)) if v is not None]
    return pd.read_parquet(path, filters=filters or None)


def load_run_results(path: Path = RESULTS_PATH) -> pd.DataFrame:
    """Load the DS1 result ids each run claims (one row per id and key).

    Filter on valid before joining to DS1; ids from before the DS1 restore point at unrelated rows.
    """
    return pd.read_parquet(path)


def load_run_rows(path: Path = ROWS_PATH) -> pd.DataFrame:
    """Load the run each DS1 row ran in (one row per id: routine, start, claimed).

    Merge on id to group the spine by run; ad-hoc rows outside every run are absent.
    """
    return pd.read_parquet(path)
