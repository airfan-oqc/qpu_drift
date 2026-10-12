import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..._io import write_table
from ...paths import OUTPUT_INFO
from ...sources.benchmarking.load import load_kpi_l2
from ...sources.hardware_models.load import load_index, load_values
from ...sources.measurements import load_measurements, valid_ids
from ...sources.output_info import iter_files
from .clean import (
    assign_rows,
    attach_ds2,
    channel_qubits,
    differs,
    ds2_series,
    ds3_link,
    entity_rows,
    result_rows,
    run_row,
    run_window,
    snapshots_around,
)
from .constants import DS2_TEMPLATES, ROUTINES
from .load import DERIVED_DIR as DEFAULT_OUT

SCRIPT_ID = "qpu_drift.derived.run_map.build"

TABLES = {
    "runs": "runs.parquet",
    "entities": "run_entities.parquet",
    "results": "run_results.parquet",
    "rows": "run_rows.parquet",
}


def build_tables(files, index: pd.DataFrame, values: pd.DataFrame, kpi: pd.DataFrame,
                 spine: pd.DataFrame, root: Path) -> dict[str, pd.DataFrame]:
    """Assemble the run, run-entity and run-result tables from the parsed files."""
    runs = pd.DataFrame([
        {"routine": routine, "start": start, "file": path.relative_to(root).as_posix(),
         "window": run_window(start), **run_row(oi, routine)}
        for path, start, routine, oi in files
    ])
    runs["start"] = runs["start"].astype("datetime64[ns]")
    runs["n_warnings"] = runs["n_warnings"].astype("Int64")
    runs = runs.sort_values(["start", "routine"], ignore_index=True)
    if runs.duplicated(["routine", "start"]).any():
        sys.exit("output_info has two files for one (routine, start); the run key is not unique")

    snaps = index.loc[index["loads_ok"], "timestamp"]
    runs["snap_before"], runs["snap_after"] = snapshots_around(runs["start"], snaps)
    bench = runs["routine"] == "benchmarking"
    runs["ds3_created_at"] = runs["start"].where(bench).map(ds3_link(kpi)).astype("datetime64[ns]")

    owners = channel_qubits(values)
    entities = pd.concat(
        [entity_rows(oi, routine, owners).assign(routine=routine, start=start)
         for _, start, routine, oi in files if routine != "benchmarking"],
        ignore_index=True,
    )
    entities["start"] = entities["start"].astype("datetime64[ns]")
    entities = attach_ds2(entities, runs, ds2_series(values))
    lead = ["routine", "start", "entity", "qubit", "param"]
    entities = entities[lead + [c for c in entities if c not in lead]]

    results = pd.DataFrame(
        [(routine, start, key, step, rid)
         for _, start, routine, oi in files for key, step, rid in result_rows(oi, routine)],
        columns=["routine", "start", "key", "step", "result_id"],
    )
    results["start"] = results["start"].astype("datetime64[ns]")
    results["valid"] = valid_ids(results["result_id"], results["start"], spine)
    rows = assign_rows(results, spine)
    return {"runs": runs, "entities": entities, "results": results, "rows": rows}


def main(argv: list[str] | None = None) -> None:
    """Build the run map: runs, per-entity outcomes and claimed result ids."""
    parser = argparse.ArgumentParser(
        description="Build the run map from output_info, DS2 and DS3."
    )
    parser.add_argument("--output-info", type=Path, default=OUTPUT_INFO,
                        help="output_info root (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    args = parser.parse_args(argv)

    if not args.output_info.is_dir():
        sys.exit(f"output_info root not found: {args.output_info}")
    files = list(iter_files(args.output_info, ROUTINES))
    if not files:
        sys.exit(f"No output_info files under {args.output_info}")
    try:
        index, values, kpi = load_index(), load_values(), load_kpi_l2()
        spine = load_measurements()[["id", "time_start", "time_end"]]
    except FileNotFoundError as e:
        sys.exit(f"Missing input table ({e.filename}); run build-hardware-models, "
                 "build-benchmarking-l2 and build-measurements first.")
    values = values[values["path_template"].isin(DS2_TEMPLATES)]

    tables = build_tables(files, index, values, kpi, spine, args.output_info)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "source": str(args.output_info),
        "out_dir": str(args.out_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID,
        "n_files": len(files),
    }
    for name, df in tables.items():
        write_table(df, args.out_dir / TABLES[name], run_meta)

    summarise(tables, args.out_dir)


def summarise(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Print row counts and the write-through check per routine and param."""
    runs, ent = tables["runs"], tables["entities"]
    print(f"runs         : {len(runs):,}  {runs['routine'].value_counts().to_dict()}")
    print(f"entities     : {len(ent):,}")
    res = tables["results"]
    print(f"result ids   : {len(res):,}  ({int((~res['valid']).sum()):,} not valid in DS1)")
    rows = tables["rows"]
    print(f"DS1 rows     : {len(rows):,} in a run ({int(rows['claimed'].sum()):,} claimed)")
    print(f"ds3 linked   : {int(runs['ds3_created_at'].notna().sum())} benchmarking runs")
    have = ent.dropna(subset=["before", "after", "old", "new"])
    check = have.groupby(["routine", "param"]).apply(
        lambda g: pd.Series({
            "rows": len(g),
            "old_is_before": 1 - differs(g["old"], g["before"]).mean(),
            "new_is_after": 1 - differs(g["new"], g["after"]).mean(),
            "changed": g["changed"].mean(),
            "written_if_changed": g.loc[g["changed"].to_numpy(), "written"].mean(),
            "written_if_unchanged": g.loc[~g["changed"].to_numpy(), "written"].mean(),
        }),
        include_groups=False,
    )
    print("write-through (rows with DS2 on both sides):")
    print(check.round(4).to_string())
    print(f"wrote        : {out_dir}/{{{','.join(TABLES.values())}}}")


if __name__ == "__main__":
    main()
