import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def latest_file(directory: Path, pattern: str) -> Path | None:
    """Return the last file in directory matching pattern by name, or None.

    Raw exports carry their date in the name, so the last name is the newest.
    """
    matches = sorted(directory.glob(pattern))
    return matches[-1] if matches else None


def write_table(df: pd.DataFrame, out: Path, run_meta: dict) -> None:
    """Write a table to Parquet (zstd) with run_meta stamped on the schema metadata."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    existing = table.schema.metadata or {}
    stamped = {k.encode(): str(v).encode() for k, v in run_meta.items()}
    table = table.replace_schema_metadata({**existing, **stamped})
    # temp file + rename so a crash mid-write can't truncate an existing table
    tmp = out.with_name(out.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, out)
