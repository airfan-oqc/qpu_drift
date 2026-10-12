import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..._io import latest_file, write_table
from ...paths import CLEAN, RAW
from .clean import clean_wide
from .constants import MIN_DENSITY

RAW_DIR = RAW
RAW_GLOB = "fridge_lx2_grafana_*.csv"
DEFAULT_OUT = CLEAN / "fridge"
SCRIPT_ID = "qpu_drift.sources.fridge.build"

TABLES = {
    "channels": "channels.parquet",
    "catalog": "channel_catalog.parquet",
}


def _load_raw(path: Path) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Read the raw CSV with 'undefined' as NaN and column 0 as a UTC timestamp index."""
    df = pd.read_csv(path, low_memory=False, na_values=["undefined"])
    epoch_ms = pd.to_numeric(df.iloc[:, 0], errors="coerce")
    if epoch_ms.isna().any():
        bad = int(epoch_ms.isna().sum())
        raise ValueError(
            f"{path}: column 0 must be integer epoch-ms timestamps; "
            f"{bad} row(s) could not be parsed as numbers"
        )
    timestamp = pd.DatetimeIndex(pd.to_datetime(epoch_ms.astype("int64"), unit="ms", utc=True))
    # replica collapsing aligns on timestamp, so a repeated row would be ambiguous
    if timestamp.has_duplicates:
        dupes = timestamp[timestamp.duplicated()].unique()
        raise ValueError(
            f"{path}: {len(dupes)} duplicate timestamp(s), e.g. {dupes[0]}; "
            "re-export without overlapping queries"
        )
    return df, timestamp


def main(argv: list[str] | None = None) -> None:
    """Read the raw export, clean it, and write the channels + catalog parquets."""
    parser = argparse.ArgumentParser(
        description="Build the clean fridge table from the raw Grafana export."
    )
    parser.add_argument("--raw", type=Path, default=None,
                        help=f"raw Grafana CSV (default: newest {RAW_GLOB} in {RAW_DIR})")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    parser.add_argument("--min-density", type=float, default=MIN_DENSITY,
                        help="global density floor; a channel below it is still kept "
                             "if it is near-complete within its own coverage window "
                             "(default: %(default)s; use 0 to disable the floor)")
    args = parser.parse_args(argv)

    raw_path = args.raw or latest_file(RAW_DIR, RAW_GLOB)
    if raw_path is None:
        sys.exit(
            f"No raw export found: pass --raw <path> or drop a {RAW_GLOB} file in "
            f"{RAW_DIR}\n(export the Grafana CSV there; see "
            f"datasheets/04_fridge_environment.md, section 3)."
        )
    if not raw_path.exists():
        sys.exit(f"Raw export not found: {raw_path}")

    df, timestamp = _load_raw(raw_path)
    wide, catalog = clean_wide(df, timestamp, min_density=args.min_density)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "source": str(raw_path),
        "out_dir": str(args.out_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID,
        "min_density": args.min_density,
        "n_channels": int(catalog["kept"].sum()),
    }
    # write_table drops the index, so keep timestamp as a column; load_fridge restores it
    write_table(wide.reset_index(), args.out_dir / TABLES["channels"], run_meta)
    write_table(catalog, args.out_dir / TABLES["catalog"], run_meta)

    summarise(raw_path, wide, catalog, args.out_dir)


def summarise(raw_path: Path, wide: pd.DataFrame, catalog: pd.DataFrame,
              out_dir: Path) -> None:
    """Print a short build summary."""
    kept = int(catalog["kept"].sum())
    dropped = int((~catalog["kept"]).sum())
    density = float(wide.notna().mean().mean())
    print(f"raw          : {raw_path}")
    print(f"rows         : {len(wide):,}  "
          f"span {wide.index.min()} .. {wide.index.max()}")
    print(f"channels     : {kept} kept / {dropped} dropped "
          f"({len(catalog)} physical candidates)")
    print(f"density      : {density:.4f}")
    print(f"wrote        : {out_dir}/{{{','.join(TABLES.values())}}}")


if __name__ == "__main__":
    main()
