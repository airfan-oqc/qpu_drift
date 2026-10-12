import argparse
import sys
from pathlib import Path

import pandas as pd

from ... import decode
from ..._io import write_table
from ...paths import DS1_FETCH_ROOT, OUTPUT_INFO
from ..measurements.clean import valid_ids
from ..measurements.load import DEFAULT_PATH
from .constants import MEASUREMENT_TYPE_METRIC, METRIC_MEASUREMENT_TYPE
from .load import CLEAN_DIR as DEFAULT_OUT
from .load import KPI_L2_PATH, load_kpi_l2
from .output_info import (
    iter_output_info,
    normalise_element,
    output_info_result_ids,
)

SERVER_ROOT = "/data/calibration_restore/oqc_calibration_routines_data/calibration_env"

DEFAULT_SPINE = DEFAULT_PATH          # datasheet 1 spine the DS1 ids resolve against
FETCH_LIST = "ds1_fetch.txt"          # rsync --files-from list (unique payloads)
MANIFEST_TABLE = "ds1_manifest.parquet"

SCRIPT_DS1_MANIFEST = "qpu_drift.sources.benchmarking.build:build_l2"
SCRIPT_DECODE_DS1 = "qpu_drift.sources.benchmarking.recover:decode_ds1"

_GROUP_METRICS = ["T1", "T2"]  # group-keyed: the pointer is group1/group2 in output_info



def read_output_info_ids(cache_dir: Path = OUTPUT_INFO) -> pd.DataFrame:
    """Read every result_ids pointer from the output_info cache, group keys included."""
    rows: list[dict] = []
    for oi, measured_at in iter_output_info(cache_dir):
        rows.extend(output_info_result_ids(oi, measured_at))
    cols = ["metric", "key", "element", "result_id", "measured_at"]
    return pd.DataFrame(rows, columns=cols)


def ds1_candidates(kpi: pd.DataFrame, oi_ids: pd.DataFrame) -> pd.DataFrame:
    """Pair every ds1 cell with its candidate DS1 result_ids.

    T1/T2 cells get both group pointers; the wrong one drops out at decode.
    """
    ds1 = kpi.loc[kpi["value_source"] == "ds1",
                  ["created_at", "metric", "element", "measured_at", "result_id"]]

    element_keyed = ds1[ds1["result_id"].notna()].copy()

    group_keyed = ds1[ds1["result_id"].isna()].drop(columns="result_id")
    group_ids = oi_ids.loc[oi_ids["metric"].isin(_GROUP_METRICS),
                           ["measured_at", "metric", "result_id"]]
    group_keyed = group_keyed.merge(group_ids, on=["measured_at", "metric"], how="inner")

    candidates = pd.concat([element_keyed, group_keyed], ignore_index=True)
    candidates["result_id"] = pd.to_numeric(candidates["result_id"]).astype("int64")
    return candidates


def resolve_and_guard(candidates: pd.DataFrame, spine: pd.DataFrame) -> pd.DataFrame:
    """Map candidate result_ids to DS1 payloads, keeping type- and time-guarded rows.

    See datasheet 1 for the guards.
    """
    keyed = spine[["id", "measurement_type", "time_start", "relative_filepath"]]
    merged = candidates.merge(keyed, left_on="result_id", right_on="id", how="left")
    ok = valid_ids(merged["result_id"], merged["measured_at"], spine,
                   expected_type=merged["metric"].map(METRIC_MEASUREMENT_TYPE))
    return merged[ok].drop(columns="id").reset_index(drop=True)


def write_manifest(manifest: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Write the rsync list of unique payloads and the per-cell manifest table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fetch_list = out_dir / FETCH_LIST
    unique_paths = sorted(manifest["relative_filepath"].dropna().unique())
    fetch_list.write_text("\n".join(unique_paths) + ("\n" if unique_paths else ""))

    manifest_path = out_dir / MANIFEST_TABLE
    write_table(manifest, manifest_path, {"script": SCRIPT_DS1_MANIFEST,
                                          "n_rows": len(manifest),
                                          "n_payloads": len(unique_paths)})
    return fetch_list, manifest_path


def rsync_command(fetch_list: Path, fetch_root: Path) -> str:
    """Return the DS1 rsync command (fill in user@host)."""
    return (
        f"rsync -a --files-from={fetch_list} "
        f"user@lx2:{SERVER_ROOT}/ {fetch_root}/"
    )


def decode_values(manifest: pd.DataFrame, fetch_root: Path) -> pd.DataFrame:
    """Decode the fetched payloads and join their fitted values back onto the manifest cells.

    Returns one row per recovered cell; unfetched payloads are skipped with a warning.
    """
    objs = []
    missing = 0
    for rel in sorted(manifest["relative_filepath"].dropna().unique()):
        path = fetch_root / rel
        if not path.exists():
            missing += 1
            continue
        try:
            objs.append(decode.payload(path))
        except decode.PayloadError:
            missing += 1
    if missing:
        print(f"warning: {missing} manifest payload(s) not found under {fetch_root} "
              f"- run the rsync step first", file=sys.stderr)

    decoded = decode.results_frame(objs)
    if decoded.empty:
        return pd.DataFrame(columns=["created_at", "metric", "element",
                                     "result_id", "value", "error", "r2"])

    decoded = decoded[decoded["measurement_type"].isin(MEASUREMENT_TYPE_METRIC)].copy()
    decoded["metric"] = decoded["measurement_type"].map(MEASUREMENT_TYPE_METRIC)
    decoded["element"] = [
        normalise_element(metric, target)
        for metric, target in zip(decoded["metric"], decoded["target"])
    ]
    decoded = decoded.rename(columns={"id": "result_id", "new": "value"})
    decoded = decoded[["result_id", "metric", "element", "value", "error", "r2"]]

    filled = manifest.merge(decoded, on=["result_id", "metric", "element"], how="inner")
    filled = filled[filled["value"].notna()]
    filled = filled.drop_duplicates(["created_at", "metric", "element"], keep="first")
    return filled[["created_at", "metric", "element",
                   "result_id", "value", "error", "r2"]].reset_index(drop=True)


def fill_kpi(kpi: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Return a KPI copy with recovered DS1 values filled into still-null ds1 cells.

    Also backfills the resolved T1/T2 result_id where the cell had none.
    """
    out = kpi.copy()
    keys = ["created_at", "metric", "element"]
    recovered = (
        values[keys + ["value", "error", "result_id"]]
        .drop_duplicates(keys)
        .rename(columns={"value": "_ds1_value", "error": "_ds1_error",
                         "result_id": "_ds1_result_id"})
    )
    # left merge with a de-duped right keeps row order and one row per cell
    merged = out.merge(recovered, on=keys, how="left")

    fillable = ((out["value_source"] == "ds1") & out["value"].isna()).to_numpy()
    ds1_value = merged["_ds1_value"].to_numpy()
    ds1_error = merged["_ds1_error"].to_numpy()

    take = fillable & ~pd.isna(ds1_value)
    take_err = fillable & ~pd.isna(ds1_error)
    out["value"] = out["value"].where(~take, ds1_value)
    out["error"] = out["error"].where(~take_err, ds1_error)

    # backfill the T1/T2 group id only where missing; keep ids as nullable ints
    out["result_id"] = out["result_id"].astype("Int64")
    ds1_result_id = merged["_ds1_result_id"].astype("Int64")
    take_id = fillable & out["result_id"].isna().to_numpy() & ds1_result_id.notna().to_numpy()
    out["result_id"] = out["result_id"].where(~take_id, ds1_result_id)
    return out


def decode_ds1(argv: list[str] | None = None) -> None:
    """Decode fetched payloads and fill the ds1 values into kpi_enriched in place."""
    parser = argparse.ArgumentParser(
        description="Decode the DS1 payloads fetched via the manifest, then fill "
                    "the recovered ds1 values into kpi_enriched in place.")
    parser.add_argument("--kpi", type=Path, default=KPI_L2_PATH,
                        help="Level-2 enriched KPI parquet (default: %(default)s)")
    parser.add_argument("--manifest", type=Path, default=None,
                        help=f"manifest parquet (default: <out-dir>/{MANIFEST_TABLE})")
    parser.add_argument("--fetch-root", type=Path, default=DS1_FETCH_ROOT,
                        help="root the payloads were rsynced to (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="Level-2 output dir (default: %(default)s)")
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (args.out_dir / MANIFEST_TABLE)
    if not Path(manifest_path).exists():
        sys.exit(f"Manifest not found: {manifest_path}\n"
                 f"Run `build-benchmarking-l2` first (it emits the manifest).")

    manifest_df = pd.read_parquet(manifest_path)
    values = decode_values(manifest_df, args.fetch_root)

    kpi = load_kpi_l2(args.kpi)
    filled = fill_kpi(kpi, values)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"script": SCRIPT_DECODE_DS1, "n_values": len(values)}
    # idempotent: fill_kpi only touches still-null ds1 cells
    write_table(filled, args.kpi, meta)

    n_ds1 = int((kpi["value_source"] == "ds1").sum())
    print(f"recovered values : {len(values):,} of {n_ds1:,} ds1 cells")
    print(f"by metric        : {values['metric'].value_counts().to_dict()}")
    print(f"finalised        : {args.kpi}  (ds1 values filled in place)")


if __name__ == "__main__":
    decode_ds1()
