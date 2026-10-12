from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep

import pandas as pd
from dotenv import load_dotenv
from qcaas_client.client import OQCClient

from ...ids import DEFAULT_QPU_ID
from ...paths import RAW

DEFAULT_URL = "https://cloud.int.oqc.app/"
DEFAULT_START = date(2024, 5, 24)  # API floor; nothing is served before this date
DEFAULT_OUT = RAW / "benchmarking" / "benchmarking_json"
MANIFEST_NAME = "_manifest.parquet"
SCRIPT_ID = "qpu_drift.sources.benchmarking.fetch"
PROGRESS_EVERY = 50


@dataclass(frozen=True)
class Settings:
    """Runtime settings from the environment."""

    url: str
    qpu_id: str
    auth_token: str | None

    @classmethod
    def from_env(cls, *, require_token: bool = False) -> Settings:
        """Build settings from env vars (loads .env first); the token is optional by default."""
        load_dotenv()
        token = os.environ.get("OQC_AUTH_TOKEN")
        if require_token and not token:
            raise RuntimeError(
                "OQC_AUTH_TOKEN is not set. Copy .env.example to .env and set it, "
                "or work from the local cache (no token needed)."
            )
        return cls(
            url=os.environ.get("OQC_URL", DEFAULT_URL),
            qpu_id=os.environ.get("OQC_QPU_ID", DEFAULT_QPU_ID),
            auth_token=token,
        )


def make_client(settings: Settings | None = None) -> OQCClient:
    """Build an authenticated OQCClient (needs OQC_AUTH_TOKEN)."""
    settings = settings or Settings.from_env(require_token=True)
    if not settings.auth_token:
        raise RuntimeError(
            "OQC_AUTH_TOKEN is not set; cannot create an authenticated client."
        )
    return OQCClient(url=settings.url, authentication_token=settings.auth_token)


def daterange(start: date, end: date) -> Iterator[date]:
    """Yield every date from start to end, inclusive."""
    for n in range(int((end - start).days) + 1):
        yield start + timedelta(days=n)


def _created_key(created_at: str) -> str:
    """Return a filesystem-safe key for a created_at string."""
    return created_at.replace(":", "").replace(" ", "T").replace(".", "_")


def _cache_range(
    get_cal: Callable[[date], dict],
    start: date,
    end: date,
    cache_dir: Path,
    *,
    sleep_s: float = 0.0,
    log_every: int = PROGRESS_EVERY,
) -> pd.DataFrame:
    """Query each date in [start, end] via get_cal, cache new payloads and merge the manifest."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    total = int((end - start).days) + 1
    rows: list[dict] = []
    for i, d in enumerate(daterange(start, end), start=1):
        cal = get_cal(d)
        created_at = None
        if isinstance(cal, dict) and cal:
            created_at = cal.get("general", {}).get("created_at")
        if created_at:
            path = cache_dir / f"{_created_key(created_at)}.json"
            if not path.exists():
                path.write_text(json.dumps(cal))
            status = "ok"
        elif cal:
            # Payload with no general.created_at: record it but don't cache it.
            status = "malformed"
        else:
            status = "empty"
        rows.append({"query_date": pd.Timestamp(d), "created_at": created_at,
                     "status": status})
        if log_every and (i % log_every == 0 or i == total):
            print(f"  fetched {i}/{total} dates ({d})", file=sys.stderr)
        if sleep_s:
            sleep(sleep_s)

    new = pd.DataFrame(rows)
    existing = load_manifest(cache_dir)
    if existing is not None and len(existing):
        manifest = (
            pd.concat([existing, new], ignore_index=True)
            .drop_duplicates(subset="query_date", keep="last")
            .sort_values("query_date")
            .reset_index(drop=True)
        )
    else:
        manifest = new
    manifest.to_parquet(cache_dir / MANIFEST_NAME, index=False)
    return manifest


def fetch_calibration_range(
    client: OQCClient,
    qpu_id: str,
    start: date,
    end: date,
    cache_dir: Path,
    *,
    sleep_s: float = 0.0,
) -> pd.DataFrame:
    """Cache a date range using the SDK client, deduplicated by created_at."""
    def get_cal(d: date) -> dict:
        return client.get_calibration(qpu_id=qpu_id, date_filter=d.strftime("%Y-%m-%d"))

    return _cache_range(get_cal, start, end, cache_dir, sleep_s=sleep_s)


def load_manifest(cache_dir: Path = DEFAULT_OUT) -> pd.DataFrame | None:
    """Load a previously written manifest, or None if absent."""
    path = Path(cache_dir) / MANIFEST_NAME
    return pd.read_parquet(path) if path.exists() else None


def load_cached_calibrations(cache_dir: Path = DEFAULT_OUT) -> list[dict]:
    """Load every cached calibration payload."""
    out: list[dict] = []
    for path in sorted(Path(cache_dir).glob("*.json")):
        if path.name.startswith("_"):
            continue
        out.append(json.loads(path.read_text()))
    return out


def _iso_date(value: str) -> date:
    """Parse a YYYY-MM-DD string into a date (argparse type)."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an ISO date (YYYY-MM-DD), got {value!r}"
        ) from exc


def main(argv: list[str] | None = None) -> None:
    """Fetch a date range from the QCaaS API into the local raw cache."""
    parser = argparse.ArgumentParser(
        description="Fetch and cache QCaaS calibration records (dedup by created_at)."
    )
    parser.add_argument("--start", type=_iso_date, default=DEFAULT_START,
                        help="first query date, YYYY-MM-DD (default: %(default)s, "
                             "the API floor)")
    parser.add_argument("--end", type=_iso_date, default=None,
                        help="last query date, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="cache directory (default: %(default)s)")
    parser.add_argument("--qpu-id", default=None,
                        help="QPU id to query (default: OQC_QPU_ID env / built-in)")
    parser.add_argument("--sleep", type=float, default=0.0, dest="sleep_s",
                        help="seconds to sleep between requests (default: %(default)s)")
    args = parser.parse_args(argv)

    end = args.end or datetime.now(timezone.utc).date()
    if end < args.start:
        sys.exit(f"--end {end} is before --start {args.start}")

    try:
        settings = Settings.from_env(require_token=True)
        client = make_client(settings)
    except RuntimeError as exc:
        sys.exit(str(exc))

    qpu_id = args.qpu_id or settings.qpu_id
    print(f"fetching {args.start} .. {end} for {qpu_id}", file=sys.stderr)
    manifest = fetch_calibration_range(
        client, qpu_id, args.start, end, args.out_dir, sleep_s=args.sleep_s
    )
    summarise(manifest, args.out_dir)


def summarise(manifest: pd.DataFrame, out_dir: Path) -> None:
    """Print a short fetch summary to stdout and any malformed dates to stderr."""
    status = manifest["status"].value_counts()
    cached = sorted(p.name for p in Path(out_dir).glob("*.json"))
    unique = manifest.loc[manifest["created_at"].notna(), "created_at"]
    print(f"cache        : {out_dir}")
    print(f"query dates  : {len(manifest):,}  "
          f"ok {status.get('ok', 0):,} / empty {status.get('empty', 0):,} "
          f"/ malformed {status.get('malformed', 0):,}")
    print(f"unique runs  : {unique.nunique():,} distinct created_at  "
          f"({len(cached):,} json files on disk)")
    if len(unique):
        print(f"created_at   : span {unique.min()} .. {unique.max()}")
    n_malformed = int(status.get("malformed", 0))
    if n_malformed:
        bad = manifest.loc[manifest["status"] == "malformed", "query_date"].tolist()
        print(f"warning: {n_malformed} malformed payload(s) at {bad}", file=sys.stderr)


if __name__ == "__main__":
    main()
