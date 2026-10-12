import argparse
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from ..._io import write_table
from ...ids import DEFAULT_QPU_ID
from ...paths import CLEAN
from .clean import (
    CANONICAL_SERVER_ROOT,
    ChangelogAccumulator,
    PathCollisionError,
    build_index,
    build_paths,
    build_values,
    leaves_by_path,
    parse_date,
    sha256_of,
    timestamp_from_path,
    walk_model,
)
from .load import decode_snapshot

DEFAULT_OUT = CLEAN / "hardware_models"
DEFAULT_SOURCE = CANONICAL_SERVER_ROOT

SCRIPT_ID = "qpu_drift.sources.hardware_models.build"

PROGRESS_EVERY = 200  # emit a progress line every N snapshots while decoding

TABLES = {  # clean tables written under --out (see datasheet 2)
    "index": "hardware_model_index.parquet",
    "values": "hardware_model_values.parquet",
    "paths": "hardware_model_paths.parquet",
}


def _fmt_dur(secs: float) -> str:
    """Format seconds as a compact duration like 1m05s."""
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s"


def _log_progress(done: int, total: int, ts, started: float, n_runs: int) -> None:
    """Print one decode-progress line to stderr (count, %, date, elapsed, ETA)."""
    elapsed = time.monotonic() - started
    rate = done / elapsed if elapsed else 0.0
    eta = (total - done) / rate if rate else 0.0
    print(
        f"  {done}/{total} ({done / total:5.1%})  "
        f"snapshot {ts:%Y-%m-%d}  {n_runs:,} runs so far  "
        f"elapsed {_fmt_dur(elapsed)}  eta {_fmt_dur(eta)}",
        file=sys.stderr, flush=True,
    )


def stream_build(source: Path, qpu_id: str,
                 start: date | None, end: date | None) -> tuple[list[dict], list[dict]]:
    """Decode the archive one snapshot at a time, folding each into the change-log.

    Return (index_metas, runs); failed decodes stay in the index with loads_ok=False.
    Undated files are skipped and the start/end filter is inclusive.
    """
    if not source.exists():
        sys.exit(
            f"Source archive not found: {source}\n"
            f"Point --source at the server's calibrations/ directory (you must be "
            f"on the VPN with the archive reachable as a filesystem path)."
        )
    files = sorted(source.rglob("*.gz"))  # dated path => chronological order
    if not files:
        sys.exit(f"No .gz snapshots under {source}.")

    # pre-scan paths only (no decode) so progress knows the total
    dated: list[tuple[Path, Path, pd.Timestamp]] = []
    skipped_undated = skipped_range = failed = 0
    for fp in files:
        rel = fp.relative_to(source)
        ts = timestamp_from_path(rel.as_posix())
        if ts is None:
            skipped_undated += 1
            continue
        if (start and ts.date() < start) or (end and ts.date() > end):
            skipped_range += 1
            continue
        dated.append((fp, rel, ts))

    total = len(dated)
    if not total:
        sys.exit("No snapshots left after the date filter.")
    filt = f" (filtered from {total + skipped_range} by date)" if skipped_range else ""
    print(f"decoding {total} snapshot(s){filt} from {source} ...",
          file=sys.stderr, flush=True)

    acc = ChangelogAccumulator()
    index_metas: list[dict] = []
    started = time.monotonic()
    for i, (fp, rel, ts) in enumerate(dated, start=1):
        raw = fp.read_bytes()
        meta = {
            "sha256": sha256_of(raw),
            "timestamp": ts,
            "qpu_id": qpu_id,
            # canonical server path, stable even if source is a local mirror
            "server_path": (CANONICAL_SERVER_ROOT / rel).as_posix(),
            "size_bytes": len(raw),
            "loads_ok": True,
            "model_class": None,
        }
        try:
            model = decode_snapshot(fp)
            leaves = leaves_by_path(walk_model(model))
            meta["model_class"] = type(model).__name__
        except (SystemExit, PathCollisionError):
            raise  # a walker bug must stop the build, not mark one file as bad
        except ImportError as exc:  # wrong env: stop rather than mark every file as bad
            sys.exit(f"{exc}\nRun the build there, e.g.:\n"
                     f"  PYTHONPATH=src .venv-maac/bin/python -m {SCRIPT_ID}")
        except Exception as exc:  # noqa: BLE001 - one bad snapshot must not sink the run
            print(f"warning: decode/melt failed for {rel}: {exc}", file=sys.stderr)
            meta["loads_ok"] = False
            failed += 1
            acc.break_runs()  # values at this snapshot are unknown: don't carry runs across
            index_metas.append(meta)
            continue
        acc.add(meta, leaves)
        index_metas.append(meta)
        del model, leaves  # free the heavy decoded snapshot before the next read
        if i % PROGRESS_EVERY == 0 or i == total:
            _log_progress(i, total, ts, started, len(acc.runs) + len(acc._open))

    if skipped_undated:
        print(f"warning: skipped {skipped_undated} file(s) with no dated path",
              file=sys.stderr)
    if failed:
        print(f"warning: {failed} snapshot(s) failed to decode", file=sys.stderr)

    runs = acc.finalise()
    if not runs:
        sys.exit("No value runs produced (every snapshot failed to decode?).")
    return index_metas, runs


def write_tables(index: pd.DataFrame, values: pd.DataFrame, paths: pd.DataFrame,
                 out_dir: Path, source: Path, start: date | None,
                 end: date | None, qpu_id: str) -> None:
    """Write the index/values/paths tables with reproducibility metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "qpu_id": qpu_id,
        "source": str(source),
        "start": start.isoformat() if start else "",
        "end": end.isoformat() if end else "",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "script": SCRIPT_ID,
        "n_snapshots": len(index),
    }
    write_table(index, out_dir / TABLES["index"], run_meta)
    write_table(values, out_dir / TABLES["values"], run_meta)
    write_table(paths, out_dir / TABLES["paths"], run_meta)


def summarise(index: pd.DataFrame, values: pd.DataFrame, paths: pd.DataFrame,
              out_dir: Path, source: Path, start: date | None,
              end: date | None) -> None:
    """Print a short summary and the command to reproduce the run."""
    ok = index["loads_ok"]
    lo, hi = index["timestamp"].min(), index["timestamp"].max()
    print(f"wrote {out_dir}/{{index,values,paths}}")
    print(f"snapshots    : {len(index)}  ({int((~ok).sum())} failed decode)")
    print(f"span         : {lo}  ..  {hi}")
    print(f"value runs   : {len(values):,}  over {values['path'].nunique():,} paths")
    print(f"path templ.  : {len(paths):,}  "
          f"({int((paths['n_changes'] > 0).sum()):,} dynamic)")
    rng = ""
    if start:
        rng += f" --start {start.isoformat()}"
    if end:
        rng += f" --end {end.isoformat()}"
    print("reproduce with:")
    print(f"  PYTHONPATH=src .venv-maac/bin/python -m {SCRIPT_ID} "
          f"--source {source}{rng}")


def main(argv: list[str] | None = None) -> None:
    """Decode the server archive and write the three clean hardware-model tables."""
    parser = argparse.ArgumentParser(
        description="Decode the dated .gz archive and write the clean "
                    "index/values/paths tables (needs qat+whisqrs; run in .venv-maac).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Server archive root (the calibrations/ directory).")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="Output directory for the three tables (default: %(default)s).")
    parser.add_argument("--qpu-id", default=DEFAULT_QPU_ID,
                        help="QPU id recorded on every snapshot (the archive is one QPU).")
    parser.add_argument("--start", default=None,
                        help="Earliest snapshot date to include, YYYY-MM-DD (inclusive).")
    parser.add_argument("--end", default=None,
                        help="Latest snapshot date to include, YYYY-MM-DD (inclusive).")
    args = parser.parse_args(argv)

    start, end = parse_date(args.start), parse_date(args.end)
    index_metas, runs = stream_build(args.source, args.qpu_id, start, end)

    index = build_index(index_metas)
    values = build_values(runs)
    paths = build_paths(runs)

    write_tables(index, values, paths, args.out_dir, args.source,
                 start, end, args.qpu_id)
    summarise(index, values, paths, args.out_dir, args.source, start, end)


if __name__ == "__main__":
    main()
