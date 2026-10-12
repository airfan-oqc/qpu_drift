import binascii
import gzip
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import IO, Any

import numpy as np
import pandas as pd

PayloadSource = str | Path | bytes | IO[bytes]

__all__ = [
    "Fit",
    "FitVector",
    "PayloadError",
    "PayloadHeader",
    "Result",
    "array",
    "crossing",
    "extract",
    "extract_generic",
    "fit_vector",
    "iter_targets",
    "payload",
    "plain",
    "read_config",
    "read_fits",
    "read_header",
    "register_extractor",
    "results_frame",
]


class PayloadError(ValueError):
    """Raised when a payload is not a decodable measurement result tree."""


@dataclass(frozen=True)
class PayloadHeader:
    """Level-1 run header: the fields that match the index row."""

    id: int | None
    measurement_name: str | None
    measurement_type: str | None
    time_start: str | None
    time_end: str | None


@dataclass(frozen=True)
class Fit:
    """A fit leaf: model string, r2 (None in the older Fit format), params and covariance."""

    model: str | None
    r2: float | None
    params: Any | None
    covariance: Any | None


@dataclass(frozen=True)
class Result:
    """One fitted parameter change for one target in a measurement.

    param is the new_-stripped name, or None for verification-only types; error is the
    value's uncertainty where the extractor has one.
    """

    id: int | None
    measurement_type: str | None
    target: str
    param: str | None
    old: Any | None
    new: Any | None
    r2: float | None
    fit_model: str | None
    error: Any | None = None


def payload(source: PayloadSource) -> dict:
    """Decompress and parse a payload (path, bytes or binary file) into its raw tree.

    Raises PayloadError if it isn't gzipped JSON with a dict node.
    """
    try:
        if isinstance(source, (str, Path)):
            with gzip.open(source, "rt", encoding="utf-8") as fh:
                obj = json.load(fh)
        elif isinstance(source, (bytes, bytearray)):
            obj = json.loads(gzip.decompress(bytes(source)).decode("utf-8"))
        else:  # binary file-like
            obj = json.loads(gzip.decompress(source.read()).decode("utf-8"))
    except (OSError, gzip.BadGzipFile, EOFError) as exc:
        raise PayloadError(f"not a readable gzip payload: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PayloadError(f"payload is not valid JSON: {exc}") from exc

    if not isinstance(obj, Mapping) or not isinstance(obj.get("dict"), Mapping):
        raise PayloadError(
            "payload is not a result envelope (expected a mapping with a 'dict' node)"
        )
    return dict(obj)


def array(node: Mapping) -> np.ndarray:
    """Decode a numpyarray node into a writable ndarray.

    Reads both stored forms: hex bytes with dtype and shape (from 2024-12-09), and the
    older nested list of typed values, whose dtype numpy infers.
    """
    if not (isinstance(node, Mapping) and node.get("type") == "numpyarray"):
        raise PayloadError("node is not a numpyarray node")
    if "value" not in node and "list" in node:
        try:
            return np.array(plain(node["list"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise PayloadError(f"malformed numpyarray node: {exc}") from exc
    try:
        raw = binascii.unhexlify(node["value"])
        arr = np.frombuffer(raw, dtype=np.dtype(node["dtype"])).copy()
    except (KeyError, binascii.Error, TypeError, ValueError) as exc:
        raise PayloadError(f"malformed numpyarray node: {exc}") from exc
    shape = node.get("shape")
    if shape:
        arr = arr.reshape(tuple(shape))
    return arr


def plain(node: Any) -> Any:
    """Convert a typed tree node into plain Python values, recursively.

    Typed leaves become their Python value and numpyarray nodes become ndarrays (via
    array); other nodes that carry a dict become that dict.
    """
    if isinstance(node, list):
        return [plain(v) for v in node]
    if not isinstance(node, Mapping):
        return node
    kind = node.get("type")
    if kind == "complex":
        return complex(node["real"], node["imag"])
    if kind == "numpyint64":
        return int(node["int"])
    if kind == "numpyfloat32":
        return float(node["float"])
    if kind == "datetime":
        return node["string"]
    if kind in ("pulseshape", "fittype"):
        return node["name"]
    if kind == "numpyarray":
        return array(node)
    if isinstance(node.get("dict"), Mapping):
        return {k: plain(v) for k, v in node["dict"].items()}
    return {k: plain(v) for k, v in node.items()}


_CONFIG_KEY = re.compile(r'"config"\s*:\s*')


def read_config(path: str | Path, chunk: int = 1 << 16) -> tuple[PayloadHeader, dict]:
    """Read a payload's header and plain config block without parsing its data.

    Decompresses only as far as the config block, so big payloads (RB) stay cheap.
    Raises PayloadError if the file isn't gzip or has no complete config block.
    """
    decoder = json.JSONDecoder()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            buf = fh.read(chunk)
            while True:
                key = _CONFIG_KEY.search(buf)
                if key:
                    try:
                        config, _ = decoder.raw_decode(buf, key.end())
                        break
                    except json.JSONDecodeError:
                        pass
                more = fh.read(len(buf))
                if not more:
                    raise PayloadError("payload holds no complete config block")
                buf += more
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise PayloadError(f"not a readable gzip payload: {exc}") from exc
    try:
        head = json.loads(buf[: key.start()].rstrip().rstrip(",") + "}}")  # close the dict and envelope
        d = head["dict"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PayloadError(f"malformed payload header: {exc}") from exc
    header = PayloadHeader(
        id=d.get("id"),
        measurement_name=d.get("measurement_name"),
        measurement_type=head.get("name"),
        time_start=plain(d.get("time_start")),
        time_end=plain(d.get("time_end")),
    )
    return header, plain(config)


def _unwrap(value: Any) -> dict | None:
    """Return a typed node's inner dict, or the mapping itself, else None."""
    if isinstance(value, Mapping):
        inner = value.get("dict")
        if isinstance(inner, Mapping):
            return dict(inner)
        return dict(value)
    return None


def read_header(obj: Mapping) -> PayloadHeader:
    """Extract the level-1 run header from a decoded payload."""
    d = obj.get("dict", {})
    return PayloadHeader(
        id=d.get("id"),
        measurement_name=d.get("measurement_name"),
        measurement_type=obj.get("name"),
        time_start=d.get("time_start"),
        time_end=d.get("time_end"),
    )


def iter_targets(obj: Mapping) -> Iterator[tuple[str, dict]]:
    """Yield (target, data_dict) for each target in the level-3 data node.

    target is the payload's own key (Q{n} or C{c}-T{t}).
    """
    data = obj.get("dict", {}).get("data", {})
    if not isinstance(data, Mapping):
        return
    for target, node in data.items():
        inner = _unwrap(node)
        if inner is not None:
            yield target, inner


_FIT_KEYS = ("r2", "function_string", "optimised_results", "opt_vector", "model", "covariance")


def _first(node: Mapping, *keys: str) -> Any | None:
    """Return the value of the first of keys present in node, else None."""
    return next((node[k] for k in keys if k in node), None)


def read_fits(target_dict: Mapping) -> list[Fit]:
    """Collect every fit leaf under one target's result dict, in key order.

    Reads both maac formats (FittingBase and the older Fit, which has no r2) and fit lists.
    """
    fits: list[Fit] = []
    for value in target_dict.values():
        for item in value if isinstance(value, list) else [value]:
            node = _unwrap(item)
            if node is None or not any(k in node for k in _FIT_KEYS):
                continue
            fits.append(
                Fit(
                    model=_first(node, "function_string", "model"),
                    r2=node.get("r2"),
                    params=plain(_first(node, "optimised_results", "opt_vector", "params")),
                    covariance=plain(_first(node, "covariance_matrix", "cov_matrix", "covariance")),
                )
            )
    return fits


@dataclass(frozen=True)
class FitVector:
    """Fitted parameters by name, their covariance matrix (same order) and r2."""

    names: tuple[str, ...]
    values: np.ndarray
    cov: np.ndarray | None
    r2: float | None

    def value(self, name: str) -> float:
        """Return the fitted value of one parameter."""
        return float(self.values[self.names.index(name)])

    def sd(self, name: str) -> float | None:
        """Return the fit sd of one parameter, or None without a covariance or that parameter."""
        if self.cov is None or name not in self.names:
            return None
        i = self.names.index(name)
        return float(np.sqrt(self.cov[i, i]))


def fit_vector(node: Mapping) -> FitVector:
    """Read a fit node's parameter names, values, covariance and r2, in either fit schema.

    Names come from variables.list, or from 2026-05 the keys of _variables.parameters.
    Values that don't match the names become NaN; a missing or misshapen covariance gives None.
    """
    d = _unwrap(node) or {}
    variables = _unwrap(d.get("variables") or d.get("_variables")) or {}
    names = tuple(
        d.get("variable_parameters") or variables.get("list") or variables.get("parameters") or ()
    )
    raw = d.get("opt_vector", d.get("optimised_results"))
    values = np.asarray(plain(raw) if raw is not None else [], dtype=float).ravel()
    raw_cov = d.get("cov_matrix", d.get("covariance_matrix"))
    cov = np.asarray(plain(raw_cov), dtype=float) if raw_cov is not None else None
    if cov is not None and cov.shape != (len(names), len(names)):
        cov = None
    if len(values) != len(names):
        values = np.full(len(names), np.nan)
    return FitVector(names, values, cov, d.get("r2"))


def crossing(first: FitVector, second: FitVector) -> tuple[float, float | None]:
    """Return where two fitted lines (A_1 x + A_0) cross, and the sd of that point.

    The sd is first-order with independent fits (understated for close slopes), or None
    if either fit has no covariance.
    """
    a, b = first.value("A_1"), first.value("A_0")
    c, d = second.value("A_1"), second.value("A_0")
    x = (d - b) / (a - c)
    if first.cov is None or second.cov is None:
        return x, None
    grads = ({"A_1": -x / (a - c), "A_0": -1 / (a - c)}, {"A_1": x / (a - c), "A_0": 1 / (a - c)})
    var = 0.0
    for fit, grad in zip((first, second), grads):
        g = np.array([grad.get(n, 0.0) for n in fit.names])
        var += float(g @ fit.cov @ g)
    return x, float(np.sqrt(var))


def _stem(name: str) -> str:
    """Return a naive singular stem so old_frequencies matches new_frequency."""
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s"):
        return name[:-1]
    return name


def _as_scalar(value: Any) -> Any:
    """Decode a numpyarray node to an ndarray; pass any other value through."""
    if isinstance(value, Mapping) and value.get("type") == "numpyarray":
        return array(value)
    return value


def _match_old(old_by_stem: Mapping[str, Any], param: str, target: str) -> Any | None:
    """Find the old_* config value for param and target."""
    entry = old_by_stem.get(_stem(param))
    if isinstance(entry, Mapping):
        return _as_scalar(entry.get(target))
    return _as_scalar(entry) if entry is not None else None


def extract_generic(obj: Mapping) -> list[Result]:
    """Read (old, new, r2) per target and param; covers most types.

    Pairs each new_* with its old_* config value by stem; targets with no new_* still get a row.
    """
    header = obj.get("dict", {})
    mid = header.get("id")
    mtype = obj.get("name")
    config = header.get("config", {})
    cfg = config.get("dict", {}) if isinstance(config, Mapping) else {}
    old_by_stem = {
        _stem(k[len("old_"):]): v for k, v in cfg.items() if k.startswith("old_")
    }

    results: list[Result] = []
    for target, node in iter_targets(obj):
        fits = read_fits(node)
        r2 = fits[0].r2 if fits else None
        fit_model = fits[0].model if fits else None

        new_items = [(k[len("new_"):], v) for k, v in node.items() if k.startswith("new_")]
        if not new_items:
            results.append(Result(mid, mtype, target, None, None, None, r2, fit_model))
            continue
        for param, new_value in new_items:
            results.append(
                Result(
                    id=mid,
                    measurement_type=mtype,
                    target=target,
                    param=param,
                    old=_match_old(old_by_stem, param, target),
                    new=_as_scalar(new_value),
                    r2=r2,
                    fit_model=fit_model,
                )
            )
    return results


_EXTRACTORS: dict[str, Callable[[Mapping], list[Result]]] = {}


def register_extractor(
    measurement_type: str,
) -> Callable[[Callable[[Mapping], list[Result]]], Callable[[Mapping], list[Result]]]:
    """Register a per-measurement_type extractor that overrides the generic one."""

    def decorator(
        fn: Callable[[Mapping], list[Result]],
    ) -> Callable[[Mapping], list[Result]]:
        _EXTRACTORS[measurement_type] = fn
        return fn

    return decorator


def extract(obj: Mapping) -> list[Result]:
    """Extract fitted results with the type's registered extractor, else the generic one."""
    mtype = obj.get("name")
    return _EXTRACTORS.get(mtype, extract_generic)(obj)


_BENCHMARK_FIELDS: dict[str, tuple[str, str | None]] = {
    "T1": ("T1", "T1_err"),
    "T2echo": ("T2", "T2_err"),
    "RandomizedBenchmarking1q": ("fidelity", "fidelity_uncertainty"),
    "RandomizedBenchmarking2q": ("fidelity", "fidelity_uncertainty"),
    "ZMap": ("fidelity", None),
}


def _benchmark_extractor(
    value_key: str, error_key: str | None
) -> Callable[[Mapping], list[Result]]:
    """Build an extractor that reads one scalar KPI value and its error per target.

    r2 is None for the older Fit format and for ZMap, which has no fit.
    """

    def extractor(obj: Mapping) -> list[Result]:
        mid = obj.get("dict", {}).get("id")
        mtype = obj.get("name")
        results: list[Result] = []
        for target, node in iter_targets(obj):
            fits = read_fits(node)
            results.append(
                Result(
                    id=mid,
                    measurement_type=mtype,
                    target=target,
                    param=value_key,
                    old=None,
                    new=_as_scalar(node.get(value_key)),
                    r2=fits[0].r2 if fits else None,
                    fit_model=fits[0].model if fits else None,
                    error=_as_scalar(node.get(error_key)) if error_key else None,
                )
            )
        return results

    return extractor


for _mtype, (_value_key, _error_key) in _BENCHMARK_FIELDS.items():
    register_extractor(_mtype)(_benchmark_extractor(_value_key, _error_key))


@register_extractor("Ramsey")
def _ramsey_extractor(obj: Mapping) -> list[Result]:
    """Return generic results, with error the fit sd of f (new = old - f + detuning)."""
    targets = dict(iter_targets(obj))
    out = []
    for r in extract_generic(obj):
        fit = targets.get(r.target, {}).get("fit")
        out.append(replace(r, error=fit_vector(fit).sd("f") if fit is not None else None))
    return out


def _crossing_extractor(obj: Mapping) -> list[Result]:
    """Return generic results, with error the crossing sd and r2 the lower of the two fits."""
    targets = dict(iter_targets(obj))
    out = []
    for r in extract_generic(obj):
        fits = [fit_vector(f) for f in targets.get(r.target, {}).get("fits") or []]
        if len(fits) != 2:
            out.append(r)
            continue
        r2s = [f.r2 for f in fits if f.r2 is not None]
        try:
            _, sd = crossing(*fits)
        except (ValueError, ZeroDivisionError, IndexError):
            sd = None
        out.append(replace(r, error=sd, r2=min(r2s) if r2s else r.r2))
    return out


for _mtype in ("FineFrequency", "FineFreqShift", "XPi2Amp", "XPi2Drag"):
    register_extractor(_mtype)(_crossing_extractor)


def results_frame(objs: Iterable[Mapping]) -> pd.DataFrame:
    """Extract many payloads into one tidy frame, one row per id, target and param.

    Columns follow Result; no results give an empty frame with those columns.
    """
    rows = [asdict(r) for obj in objs for r in extract(obj)]
    columns = [f.name for f in Result.__dataclass_fields__.values()]
    return pd.DataFrame(rows, columns=columns)
