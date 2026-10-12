import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..._io import latest_file, write_table
from ...paths import CLEAN, RAW
from .clean import (
    build_spine,
    explode_couplings,
    explode_qubits,
    validate_spine,
)

DEFAULT_RAW_DIR = RAW
RAW_GLOB = "measurements_index_*.db"
DEFAULT_OUT = CLEAN / "measurements"
SCRIPT_ID = "qpu_drift.sources.measurements.build"

TABLES = {
    "spine": "measurements.parquet",
    "by_qubit": "measurements_by_qubit.parquet",
    "by_coupling": "measurements_by_coupling.parquet",
}


def _load_raw(path: Path) -> pd.DataFrame:
    """Read the whole result_table index (no payload decoding), read-only."""
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return pd.read_sql_query("SELECT * FROM result_table", con)
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    """Read the measurement index and write the clean, lossless spine parquets."""
    parser = argparse.ArgumentParser(
        description="Build the clean measurement spine from the raw index DB."
    )
    parser.add_argument("--raw", type=Path, default=None,
                        help=f"raw SQLite index (default: newest "
                             f"{RAW_GLOB} in {DEFAULT_RAW_DIR})")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    args = parser.parse_args(argv)

    raw_path = args.raw or latest_file(DEFAULT_RAW_DIR, RAW_GLOB)
    if raw_path is None:
        sys.exit(
            f"No raw index found: pass --raw <path> or drop a {RAW_GLOB} file in "
            f"{DEFAULT_RAW_DIR}\n(scp the server's SQLite index there; see "
            f"datasheets/01_measurements_database.md)."
        )
    if not raw_path.exists():
        sys.exit(f"Raw index not found: {raw_path}")

    raw = _load_raw(raw_path)
    spine = build_spine(raw)
    checks = validate_spine(spine)
    by_qubit = explode_qubits(spine)
    by_coupling = explode_couplings(spine)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "source": str(raw_path),
        "out_dir": str(args.out_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID,
        "n_measurements": len(spine),
    }
    write_table(spine, args.out_dir / TABLES["spine"], run_meta)
    write_table(by_qubit, args.out_dir / TABLES["by_qubit"], run_meta)
    write_table(by_coupling, args.out_dir / TABLES["by_coupling"], run_meta)

    summarise(raw_path, spine, checks, by_qubit, by_coupling, args.out_dir)


def summarise(raw_path: Path, spine: pd.DataFrame, checks: dict[str, int],
              by_qubit: pd.DataFrame, by_coupling: pd.DataFrame,
              out_dir: Path) -> None:
    """Print a short build summary; validation failures go to stderr."""
    tt = spine["target_type"].value_counts()
    print(f"raw          : {raw_path}")
    print(f"measurements : {len(spine):,}  "
          f"span {spine['time_start'].min()} .. {spine['time_start'].max()}")
    print(f"types        : {spine['measurement_type'].nunique()}  "
          f"targets {tt.get('qubit', 0):,} qubit / {tt.get('coupling', 0):,} coupling "
          f"/ {tt.get('none', 0):,} unkeyed")
    print(f"long tables  : {len(by_qubit):,} qubit-rows  "
          f"{len(by_coupling):,} coupling-rows")
    print(f"wrote        : {out_dir}/{{{','.join(TABLES.values())}}}")
    flagged = {k: v for k, v in checks.items() if v}
    if flagged:
        print(f"warning: validation found nonzero counts: {flagged}", file=sys.stderr)
    else:
        print("validation   : OK (all checks 0)")


if __name__ == "__main__":
    main()
