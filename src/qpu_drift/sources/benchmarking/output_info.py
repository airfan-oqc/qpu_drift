from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from ...ids import coupling_key, qubit
from ...paths import OUTPUT_INFO
from ..output_info import iter_files
from .constants import (
    _ELEMENT_KEYED,
    _NO_FIT_PHRASE,
    _NO_FIT_RE,
    _OI_GROUPS,
    METRIC_2Q,
)


def iter_output_info(
    cache_dir: Path = OUTPUT_INFO,
) -> Iterator[tuple[dict, pd.Timestamp]]:
    """Yield (oi, measured_at) for each readable benchmarking file, skipping bad ones.

    measured_at is the run start taken from the file path.
    """
    for _, measured_at, _, oi in iter_files(cache_dir, ("benchmarking",)):
        yield oi, measured_at


def normalise_element(metric: str, raw_key: str) -> str | None:
    """Map an output_info key (C4-T5, C4_T5, Q1, 1) to a KPI element (4-5, 1), or None."""
    try:
        if metric == METRIC_2Q:
            return coupling_key(raw_key)
        return str(qubit(raw_key))
    except ValueError:
        return None


def _warned_elements(warnings: list) -> set[tuple[str, str]]:
    """Return the (metric, element) pairs flagged by a "Did not return a ..." no-fit warning."""
    out: set[tuple[str, str]] = set()
    for w in warnings:
        m = _NO_FIT_RE.search(str(w))
        if not m:
            continue
        metric = _NO_FIT_PHRASE.get(m.group(1).strip().lower())
        if metric is None:
            continue
        element = normalise_element(metric, m.group(2))
        if element is not None:
            out.add((metric, element))
    return out


def _is_number(x) -> bool:
    """True if x is a real number, not a bool, None, NaN or dict."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not pd.isna(x)


def _by_element(metric: str, group_map: dict) -> dict[str, object]:
    """Normalise an output_info group {raw_key: v} to {element: v}."""
    out: dict[str, object] = {}
    for raw_key, v in group_map.items():
        element = normalise_element(metric, raw_key)
        if element is not None:
            out.setdefault(element, v)
    return out


def _index_by_element(metric: str, rid_map: dict) -> dict[str, object]:
    """Normalise a result_ids group to {element: result_id}; empty unless fRB or fCX."""
    if metric not in _ELEMENT_KEYED:
        return {}
    out: dict[str, object] = {}
    for raw_key, rid in rid_map.items():
        element = normalise_element(metric, raw_key)
        if element is not None:
            out[element] = rid
    return out


def output_info_rows(oi: dict, measured_at: pd.Timestamp) -> list[dict]:
    """Return the provenance rows classify needs from one output_info file (see datasheet 3).

    One row per traced element: metric, element, value, r2, warned, result_id, measured_at.
    """
    result_ids = oi.get("result_ids") or {}
    fit_quality = oi.get("fit_quality") or {}
    benchmarks = oi.get("benchmarks") or {}
    warned = _warned_elements(oi.get("warnings") or [])
    rows: list[dict] = []
    for metric, (rid_group, group) in _OI_GROUPS.items():
        rid_by_element = _index_by_element(metric, result_ids.get(rid_group) or {})
        r2_by_element = _by_element(metric, fit_quality.get(group) or {})
        val_by_element = _by_element(metric, benchmarks.get(group) or {})
        warned_here = {e for (mm, e) in warned if mm == metric}
        elements = (set(r2_by_element) | set(val_by_element)
                    | set(rid_by_element) | warned_here)
        for element in elements:
            rid = rid_by_element.get(element)
            r2 = r2_by_element.get(element)
            val = val_by_element.get(element)
            rows.append({
                "metric": metric,
                "element": element,
                "value": val if _is_number(val) else pd.NA,
                "r2": r2 if r2 is not None else pd.NA,
                "warned": (metric, element) in warned,
                "result_id": rid if rid is not None else pd.NA,
                "measured_at": measured_at,
            })
    return rows


def output_info_result_ids(oi: dict, measured_at: pd.Timestamp) -> list[dict]:
    """Return every result_ids pointer in one output_info file, including group keys.

    element is NA for group keys (T1/T2/fRO); this feeds the DS1 fetch list.
    """
    result_ids = oi.get("result_ids") or {}
    rows: list[dict] = []
    for metric, (rid_group, _group) in _OI_GROUPS.items():
        for raw_key, rid in (result_ids.get(rid_group) or {}).items():
            if rid is None:
                continue
            element = normalise_element(metric, raw_key)
            rows.append({
                "metric": metric,
                "key": raw_key,
                "element": element if element is not None else pd.NA,
                "result_id": rid,
                "measured_at": measured_at,
            })
    return rows
