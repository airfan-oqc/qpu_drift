import pandas as pd

from ..paths import DERIVED

NOISE_PATH = DERIVED / "noise" / "noise_floor.parquet"
COLUMNS = ["source", "param", "entity", "regime", "start", "end", "method", "sigma", "unit", "n"]


def load_noise_floor(path=NOISE_PATH) -> pd.DataFrame:
    """Load the shared noise-floor table, or an empty one if nothing is written yet."""
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)


def write_noise_rows(rows: pd.DataFrame, source: str, path=NOISE_PATH) -> pd.DataFrame:
    """Replace one source's rows in the noise-floor table and return the whole table.

    Other sources are left alone. Start and end are naive UTC.
    """
    missing = set(COLUMNS) - set(rows.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if (rows["source"] != source).any():
        raise ValueError(f"every row must have source {source!r}")
    old = load_noise_floor(path)
    new = pd.concat([old[old["source"] != source], rows[COLUMNS]], ignore_index=True) if len(old) else rows[COLUMNS]
    path.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(path, index=False)
    return new
