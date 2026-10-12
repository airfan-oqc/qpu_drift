import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..._io import write_table
from ...paths import DS1_FETCH_ROOT, OUTPUT_INFO
from . import clean
from .fetch import DEFAULT_OUT as DEFAULT_RAW
from .fetch import load_cached_calibrations
from .load import CLEAN_DIR as DEFAULT_OUT
from .output_info import iter_output_info, output_info_rows
from .recover import (
    DEFAULT_SPINE,
    ds1_candidates,
    read_output_info_ids,
    resolve_and_guard,
    rsync_command,
    write_manifest,
)

SCRIPT_ID = "qpu_drift.sources.benchmarking.build:main"
SCRIPT_ID_L2 = "qpu_drift.sources.benchmarking.build:build_l2"

TABLES = {
    "runs": "runs.parquet",
    "kpi": "kpi.parquet",
}
TABLES_L2 = {
    "runs": "runs_enriched.parquet",
    "kpi": "kpi_enriched.parquet",
}

INFORMATIONAL_CHECKS = {"n_good", "n_unresolved", "l2_ok_recovered"}  # never fail the build


def hard_failures(checks: dict[str, int]) -> dict[str, int]:
    """Return the nonzero hard-invariant counts in a validate_* result (empty = OK)."""
    return {k: v for k, v in checks.items()
            if v and k not in INFORMATIONAL_CHECKS}


def read_output_info(cache_dir: Path = OUTPUT_INFO) -> pd.DataFrame:
    """Read the pulled output_info tree into a provenance table.

    A readable file with no element trace adds one run-only row so its nulls settle no_result.
    """
    rows: list[dict] = []
    for oi, measured_at in iter_output_info(cache_dir):
        file_rows = output_info_rows(oi, measured_at)
        rows.extend(file_rows or [{"metric": pd.NA, "element": pd.NA,
                                   "measured_at": measured_at}])
    cols = ["metric", "element", "value", "r2", "warned", "result_id", "measured_at"]
    return pd.DataFrame(rows, columns=cols)


def _load_payloads(raw: Path) -> list[dict]:
    """Load the raw calibration cache, or exit with a hint if it's missing."""
    if not Path(raw).is_dir():
        sys.exit(
            f"Raw cache not found: {raw}\nRun `fetch-benchmarking` first "
            f"(see datasheets/03_benchmarking_files.md section 8)."
        )
    payloads = load_cached_calibrations(raw)
    if not payloads:
        sys.exit(f"No cached payloads in {raw}; run `fetch-benchmarking` first.")
    return payloads


def main(argv: list[str] | None = None) -> None:
    """Build L1: flatten the raw QCaaS cache into the gated good-value runs and KPI tables."""
    parser = argparse.ArgumentParser(
        description="Build the Level-1 (QCaaS good-value) calibration tables from "
                    "the raw JSON cache."
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                        help="raw calibration cache dir (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    args = parser.parse_args(argv)

    payloads = _load_payloads(args.raw)
    runs, kpi = clean.assemble(payloads)
    kpi = clean.gate_good_values(kpi)
    checks = clean.validate_l1(runs, kpi)
    failures = hard_failures(checks)
    if failures:
        sys.exit(f"build aborted: Level-1 validation failed {failures} - a hard "
                 f"invariant broke (a gated reject leaked through), so no table "
                 f"was written. Fix the gate before rebuilding.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "source": str(args.raw),
        "out_dir": str(args.out_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID,
        "n_runs": len(runs),
        "n_kpi_rows": len(kpi),
    }
    write_table(runs, args.out_dir / TABLES["runs"], run_meta)
    write_table(kpi, args.out_dir / TABLES["kpi"], run_meta)

    summarise_l1(args.raw, runs, kpi, checks, args.out_dir)


def build_l2(argv: list[str] | None = None) -> None:
    """Build L2: enrich the raw spine with output_info provenance, then write the DS1 manifest.

    Needs the output_info cache and the measurements index locally (see datasheet 3).
    """
    parser = argparse.ArgumentParser(
        description="Build the Level-2 (enriched) calibration tables from the raw "
                    "cache plus the pulled output_info provenance."
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                        help="raw calibration cache dir (default: %(default)s)")
    parser.add_argument("--output-info", type=Path, default=OUTPUT_INFO,
                        help="output_info cache dir (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="output directory (default: %(default)s)")
    parser.add_argument("--spine", type=Path, default=DEFAULT_SPINE,
                        help="clean measurements index / spine, for the DS1 "
                             "recovery manifest (default: %(default)s)")
    parser.add_argument("--fetch-root", type=Path, default=DS1_FETCH_ROOT,
                        help="where the DS1 rsync will land payloads "
                             "(default: %(default)s)")
    args = parser.parse_args(argv)

    if not Path(args.output_info).is_dir():
        sys.exit(
            f"output_info cache not found: {args.output_info}\nCopy both server "
            f"output_info folders first "
            f"(see datasheets/03_benchmarking_files.md section 8)."
        )
    if not Path(args.spine).exists():
        sys.exit(
            f"measurements index not found: {args.spine}\n"
            f"Level 2 resolves the DS1 recovery manifest against the measurements "
            f"index - build it first with `build-measurements` (datasheet 1)."
        )
    payloads = _load_payloads(args.raw)
    runs, kpi = clean.assemble(payloads)
    runs = clean.add_gating_regime(runs)
    prov = read_output_info(args.output_info)
    if prov.empty:
        sys.exit(
            f"output_info cache is empty: {args.output_info}\n"
            f"No *_benchmarking.json parsed - copy both server output_info "
            f"folders to populate it. Level 2 requires the cache; "
            f"`build-benchmarking` is the QCaaS-only Level-1 baseline."
        )
    # L1 good set from the same spine, taken before enrich reassigns kpi
    l1_good = clean.gate_good_values(kpi)
    kpi = clean.enrich_kpi(kpi, prov)
    # an L1 good value demoted at L2 is a hard failure
    checks = clean.validate_l2(runs, kpi, l1_good=l1_good)
    failures = hard_failures(checks)
    if failures:
        sys.exit(f"build aborted: Level-2 validation failed {failures} - a hard "
                 f"invariant broke (the classifier or coalesce regressed), so no "
                 f"table was written. Fix the classifier before rebuilding.")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "source": str(args.raw),
        "output_info": str(args.output_info),
        "out_dir": str(args.out_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID_L2,
        "n_runs": len(runs),
        "n_kpi_rows": len(kpi),
    }
    write_table(runs, args.out_dir / TABLES_L2["runs"], run_meta)
    write_table(kpi, args.out_dir / TABLES_L2["kpi"], run_meta)

    summarise_l2(args.raw, args.output_info, runs, kpi, prov, checks, args.out_dir)

    # Resolve the DS1 recovery manifest; the DS1 rsync and decode-ds1 come next.
    spine = pd.read_parquet(args.spine)
    oi_ids = read_output_info_ids(args.output_info)
    candidates = ds1_candidates(kpi, oi_ids)
    guarded = resolve_and_guard(candidates, spine)
    fetch_list, manifest_path = write_manifest(guarded, args.out_dir)

    n_ds1 = int((kpi["value_source"] == "ds1").sum())
    covered = guarded.drop_duplicates(["created_at", "metric", "element"])
    print(f"\nds1 cells      : {n_ds1:,}")
    print(f"ds1 candidates : {len(candidates):,}  "
          f"(element-keyed fRB/fCX + both-group T1/T2)")
    print(f"guarded rows   : {len(guarded):,}  "
          f"covering {len(covered):,} cells / {guarded['relative_filepath'].nunique():,} payloads")
    print(f"wrote          : {fetch_list}")
    print(f"                 {manifest_path}")
    print("\nnext - fetch the DS1 payloads (fill in user@host), then run "
          "`decode-ds1`:\n")
    print("  " + rsync_command(fetch_list, args.fetch_root))


def summarise_l1(raw: Path, runs: pd.DataFrame, kpi: pd.DataFrame,
                 checks: dict[str, int], out_dir: Path) -> None:
    """Print a short L1 build summary; validation failures go to stderr."""
    n_good = int(kpi["value"].notna().sum())
    print(f"raw          : {raw}")
    print(f"runs         : {len(runs):,}  "
          f"span {runs['created_at'].min()} .. {runs['created_at'].max()}  "
          f"({int(runs['has_openpulse'].sum()):,} with openpulse)")
    print(f"kpi rows     : {len(kpi):,}  "
          f"good values {n_good:,} / gated nulls {len(kpi) - n_good:,}")
    print(f"wrote        : {out_dir}/{{{','.join(TABLES.values())}}}")

    if hard_failures(checks):
        print(f"warning: validation found nonzero counts: {hard_failures(checks)}",
              file=sys.stderr)
    else:
        print("validation   : OK (all hard checks 0)")


def summarise_l2(raw: Path, output_info: Path, runs: pd.DataFrame,
                 kpi: pd.DataFrame, prov: pd.DataFrame,
                 checks: dict[str, int], out_dir: Path) -> None:
    """Print a short L2 build summary; validation failures go to stderr."""
    status = kpi["value_status"].value_counts()
    n_unresolved = int(status.get("unresolved", 0))
    src = kpi["value_source"].value_counts()
    regime = runs["gating_regime"].value_counts()
    print(f"raw          : {raw}")
    print(f"output_info  : {output_info}  ({len(prov):,} provenance rows)")
    print(f"runs         : {len(runs):,}  "
          f"span {runs['created_at'].min()} .. {runs['created_at'].max()}  "
          f"({int(runs['has_openpulse'].sum()):,} with openpulse)")
    print(f"gating       : era0_ungated {regime.get('era0_ungated', 0):,} "
          f"/ era1_filter {regime.get('era1_filter', 0):,} "
          f"/ era2_ungated {regime.get('era2_ungated', 0):,} "
          f"/ era3_postproc {regime.get('era3_postproc', 0):,} runs")
    print(f"kpi rows     : {len(kpi):,}  "
          f"ok {status.get('ok', 0):,} / out_of_range {status.get('out_of_range', 0):,} "
          f"/ low_r2 {status.get('low_r2', 0):,} "
          f"/ no_fit {status.get('no_fit', 0):,} "
          f"/ no_result {status.get('no_result', 0):,} "
          f"/ no_r2 {status.get('no_r2', 0):,} "
          f"/ unresolved {n_unresolved:,}")
    print(f"value_source : qcaas {src.get('qcaas', 0):,} "
          f"/ output_info {src.get('output_info', 0):,} "
          f"/ ds1 {src.get('ds1', 0):,} / none {src.get('none', 0):,}")
    print(f"wrote        : {out_dir}/{{{','.join(TABLES_L2.values())}}}")

    if hard_failures(checks):
        print(f"warning: validation found nonzero counts: {hard_failures(checks)}",
              file=sys.stderr)
    else:
        print("validation   : OK (all hard checks 0)")


if __name__ == "__main__":
    main()
