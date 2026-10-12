from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
RAW = DATA / "raw"
CLEAN = DATA / "clean"
DERIVED = DATA / "derived"

OUTPUT_INFO = RAW / "benchmarking" / "output_info"  # every routine's output_info file, YYYY/MM/DD/HH_MM_SS_<routine>.json
DS1_FETCH_ROOT = RAW / "benchmarking" / "measurement_payloads"  # where the decode-ds1 rsync lands DS1 payloads
MEASUREMENT_PAYLOADS = RAW / "measurement_payloads"  # DS1 payloads from the Phase 01 fetch lists
RAW_RAMSEY = RAW / "raw_ramsey"  # Ramsey payloads, without the results/ prefix of their relative_filepath
