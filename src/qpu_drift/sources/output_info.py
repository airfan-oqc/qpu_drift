import json
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import pandas as pd

from ..paths import OUTPUT_INFO

_PATH_RE = re.compile(r"(\d{4})/(\d{2})/(\d{2})/(\d{2})_(\d{2})_(\d{2})_(\w+)\.json$")


def parse_run_path(path: Path | str) -> tuple[pd.Timestamp, str] | None:
    """Return (start, routine) from an output_info path YYYY/MM/DD/HH_MM_SS_<routine>.json.

    start is naive server-clock time; returns None if the path doesn't match.
    """
    m = _PATH_RE.search(Path(path).as_posix())
    if not m:
        return None
    y, mo, d, hh, mm, ss = (int(g) for g in m.groups()[:6])
    return pd.Timestamp(year=y, month=mo, day=d, hour=hh, minute=mm, second=ss), m.group(7)


def iter_files(
    root: Path = OUTPUT_INFO,
    routines: Iterable[str] | None = None,
) -> Iterator[tuple[Path, pd.Timestamp, str, dict]]:
    """Yield (path, start, routine, oi) per readable output_info file, in path order.

    Unreadable files are skipped with one stderr note; non-object JSON reads as {}.
    """
    keep = None if routines is None else set(routines)
    skipped = 0
    for path in sorted(Path(root).glob("*/*/*/*.json")):
        parsed = parse_run_path(path)
        if parsed is None or (keep is not None and parsed[1] not in keep):
            continue
        try:
            oi = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        yield path, *parsed, oi if isinstance(oi, dict) else {}
    if skipped:
        print(f"warning: skipped {skipped} unreadable output_info file(s)", file=sys.stderr)
