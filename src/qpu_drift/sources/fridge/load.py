from pathlib import Path

import pandas as pd

from ...paths import CLEAN
from .clean import cold_mask

DEFAULT_PATH = CLEAN / "fridge" / "channels.parquet"


def load_fridge(path: Path = DEFAULT_PATH, cold: bool = False, **cold_kwargs) -> pd.DataFrame:
    """Load the clean fridge wide table (tz-aware UTC index).

    cold=True applies the cold mask; extra kwargs go to cold_mask (e.g. mix_max=0.03).
    """
    wide = pd.read_parquet(path)
    if "timestamp" in wide.columns:
        wide = wide.set_index("timestamp")
    if cold:
        wide = wide[cold_mask(wide, **cold_kwargs)]
    elif cold_kwargs:
        raise TypeError("cold-mask thresholds passed but cold=False; set cold=True to apply them")
    return wide
