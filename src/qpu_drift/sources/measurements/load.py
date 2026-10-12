from pathlib import Path

import pandas as pd

from ...paths import CLEAN, DS1_FETCH_ROOT, MEASUREMENT_PAYLOADS, RAW_RAMSEY

DEFAULT_PATH = CLEAN / "measurements" / "measurements.parquet"
PAYLOAD_ROOTS = (  # local payload copies and the relative_filepath prefix their layout drops
    (RAW_RAMSEY, "results/"),
    (DS1_FETCH_ROOT, ""),
    (MEASUREMENT_PAYLOADS, ""),
)


def load_measurements(
    path: Path = DEFAULT_PATH, measurement_type: str | None = None
) -> pd.DataFrame:
    """Load the clean measurement spine (one row per measurement, naive clock).

    Pass measurement_type (e.g. "Ramsey") to filter at read time.
    """
    spine = pd.read_parquet(path)
    if measurement_type is not None:
        spine = spine[spine["measurement_type"] == measurement_type]
    return spine


def local_payloads(roots=PAYLOAD_ROOTS) -> dict[str, Path]:
    """Map each local payload's spine relative_filepath to its file; roots are (dir, prefix) pairs.

    The first root wins when a payload is in more than one; missing dirs are skipped.
    """
    paths: dict[str, Path] = {}
    for root, prefix in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for p in root.rglob("*.gz"):
            paths.setdefault(prefix + p.relative_to(root).as_posix(), p)
    return paths
