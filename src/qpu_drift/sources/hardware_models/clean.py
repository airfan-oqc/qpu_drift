import enum
import hashlib
import inspect
import json
import re
from collections import deque
from datetime import date
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL_SERVER_ROOT = Path(  # server_path is rebuilt from this, even off a local mirror
    "/data/calibration_restore/oqc_calibration_routines_data/"
    "calibration_env/calibrations"
)

PATH_RE = re.compile(  # .../YYYY/MM/DD/HH-MM-SS_calibration.gz; the path is the only timestamp
    r"(?P<Y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/"
    r"(?P<H>\d{2})-(?P<M>\d{2})-(?P<S>\d{2})_calibration\.gz$"
)

VALUE_COLUMNS = ("value_int", "value_float", "value_bool", "value_str", "value_json")  # one non-null per row

DTYPES = (  # "empty" is a present but empty container, not an absent path
    "int", "float", "complex", "bool", "str", "enum", "ref", "null", "json", "empty"
)


def sha256_of(raw: bytes) -> str:
    """Return the sha256 of the snapshot bytes (integrity check and raw join key)."""
    return hashlib.sha256(raw).hexdigest()


def timestamp_from_path(rel_posix: str) -> pd.Timestamp | None:
    """Parse the naive (UTC) snapshot time from the dated archive path, or None."""
    m = PATH_RE.search(rel_posix)
    if not m:
        return None
    g = m.groupdict()
    return pd.Timestamp(
        year=int(g["Y"]), month=int(g["m"]), day=int(g["d"]),
        hour=int(g["H"]), minute=int(g["M"]), second=int(g["S"]),
    )


def parse_date(s: str | None) -> date | None:
    """Parse an ISO YYYY-MM-DD string into a date, or None."""
    if s is None:
        return None
    return date.fromisoformat(s)


def _to_jsonable(v):
    """Coerce a leaf value into JSON-native form for value_json."""
    if isinstance(v, enum.Enum):
        return v.name
    if isinstance(v, complex):
        return [v.real, v.imag]
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return [_to_jsonable(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted((_to_jsonable(x) for x in v), key=repr)
    return v


def _canonical_json(obj) -> str:
    """Return deterministic JSON text (sorted keys, tight separators) for value_json."""
    return json.dumps(_to_jsonable(obj), sort_keys=True, separators=(",", ":"))


def _blank_leaf() -> dict:
    """Return a value record with every tagged-union column set to None."""
    return {
        "dtype": None, "enum_type": None, "token": None,
        "value_int": None, "value_float": None, "value_bool": None,
        "value_str": None, "value_json": None,
    }


def classify(v) -> dict:
    """Encode a scalar leaf into dtype, value_* columns, token and enum_type.

    token is only for run detection; floats use float.hex so equal bits (even NaN) match.
    """
    rec = _blank_leaf()
    # bool before int: bool is a subclass of int
    if v is None:
        rec.update(dtype="null", token="null")
    elif isinstance(v, bool):
        rec.update(dtype="bool", value_bool=v, token=f"b:{int(v)}")
    elif isinstance(v, enum.Enum):
        rec.update(dtype="enum", value_str=v.name, enum_type=type(v).__name__,
                   token=f"e:{type(v).__name__}.{v.name}")
    elif isinstance(v, (int, np.integer)):
        v = int(v)
        rec.update(dtype="int", value_int=v, token=f"i:{v}")
    elif isinstance(v, (float, np.floating)):
        v = float(v)
        rec.update(dtype="float", value_float=v, token=f"f:{v.hex()}")
    elif isinstance(v, complex):
        rec.update(dtype="complex", value_json=_canonical_json(v),
                   token=f"c:{v.real.hex()},{v.imag.hex()}")
    elif isinstance(v, str):
        rec.update(dtype="str", value_str=v, token=f"s:{v}")
    else:
        raise TypeError(f"classify() got a non-leaf value: {type(v)!r}")
    return rec


def _json_leaf(obj) -> dict:
    """Return a json leaf (a terminal scalar array or ndarray kept as one value)."""
    rec = _blank_leaf()
    text = _canonical_json(obj)
    rec.update(dtype="json", value_json=text, token=f"j:{text}")
    return rec


def _empty_leaf(obj) -> dict:
    """Return an empty leaf: a list/dict/set that is present but has no items."""
    rec = _blank_leaf()
    text = "{}" if isinstance(obj, dict) else "[]"
    rec.update(dtype="empty", value_json=text, token=f"empty:{text}")
    return rec


def _ref_leaf(target_path: str) -> dict:
    """Return a ref leaf pointing at a shared object's canonical path."""
    rec = _blank_leaf()
    rec.update(dtype="ref", value_str=target_path, token=f"r:{target_path}")
    return rec


_SCALAR_LEAF = (bool, int, float, complex, str, enum.Enum, np.integer, np.floating)
_STABLE_KEY_ATTRS = ("index", "direction", "id", "full_id", "hardware_id", "name")


def _is_scalar_leaf(v) -> bool:
    return v is None or isinstance(v, _SCALAR_LEAF)


def stable_key(item, idx: int) -> str:
    """Key a list element by its first identity attr (index, direction, ...), else [idx]."""
    for attr in _STABLE_KEY_ATTRS:
        val = getattr(item, attr, None)
        if val is not None and not inspect.isroutine(val):
            return f"{attr}={val}"
    return f"[{idx}]"


def _object_state(obj) -> dict:
    """Return an object's instance attributes worth walking.

    Skips dunders, methods, nested classes and stale attrs shadowed by a class property.
    """
    d = getattr(obj, "__dict__", None)
    if not d:
        return {}
    cls = type(obj)
    return {
        k: val for k, val in d.items()
        if not (k.startswith("__") and k.endswith("__"))
        and not inspect.isroutine(val)
        and not isinstance(val, type)
        and not isinstance(inspect.getattr_static(cls, k, None), property)
    }


_STATE_LABEL = re.compile(r"[01]+\|[01]+")  # readout_mitigation cells such as 0|1 are fields, not ids


def _template_key(key: str) -> str:
    return key if _STATE_LABEL.fullmatch(key) else re.sub(r"\d+", "*", key)


def _template(path: str) -> str:
    """Remove the ids from a concrete path: one template per parameter.

    Numbers inside each {...} key become *, so the id kind and channel role stay
    ({Q*}, {Ch*.Q*.drive}) and field names stay literal ({amp}, {I}, {0|1}).
    """
    t = re.sub(r"\{([^{}]*)\}", lambda m: "{" + _template_key(m.group(1)) + "}", path)
    return re.sub(r"\[\d+\]", "[*]", t)


def _emit(rec: dict, path: str, entity, entity_id, out: list) -> None:
    rec["path"] = path
    rec["path_template"] = _template(path)
    rec["entity"] = entity
    rec["entity_id"] = entity_id
    out.append(rec)


def _canonical_paths(model) -> dict[int, str]:
    """Map id(obj) -> its shallowest path, found breadth-first.

    Shared objects are inlined there and become refs elsewhere (see datasheet 2).
    """
    canonical: dict[int, str] = {}
    queue: deque[tuple[object, str]] = deque([(model, "model")])
    while queue:
        v, path = queue.popleft()
        if _is_scalar_leaf(v) or isinstance(v, np.ndarray):
            continue
        if isinstance(v, dict):
            for k in v:
                queue.append((v[k], f"{path}{{{str(k)}}}"))
            continue
        if isinstance(v, (list, tuple, set, frozenset)):
            items = sorted(v, key=repr) if isinstance(v, (set, frozenset)) else list(v)
            if not items or all(_is_scalar_leaf(x) for x in items):
                continue
            for i, item in enumerate(items):
                queue.append((item, f"{path}{{{stable_key(item, i)}}}"))
            continue
        # live object: with BFS the first dequeue is the shallowest path
        oid = id(v)
        if oid in canonical:
            continue
        canonical[oid] = path
        state = _object_state(v)
        for k in sorted(state):
            queue.append((state[k], f"{path}.{k}"))
    return canonical


def _walk(v, path, entity, entity_id, out, canonical) -> None:
    """Recursively flatten v at path into value leaves in out."""
    if _is_scalar_leaf(v):
        _emit(classify(v), path, entity, entity_id, out)
        return

    if isinstance(v, np.ndarray):
        _emit(_empty_leaf([]) if v.size == 0 else _json_leaf(v),
              path, entity, entity_id, out)
        return

    if isinstance(v, dict):
        if not v:
            _emit(_empty_leaf(v), path, entity, entity_id, out)
            return
        for k in v:
            key = str(k)
            eid = entity_id if entity_id is not None else key
            _walk(v[k], f"{path}{{{key}}}", entity, eid, out, canonical)
        return

    if isinstance(v, (list, tuple, set, frozenset)):
        items = sorted(v, key=repr) if isinstance(v, (set, frozenset)) else list(v)
        if not items:
            _emit(_empty_leaf([]), path, entity, entity_id, out)
            return
        if all(_is_scalar_leaf(x) for x in items):
            # a terminal scalar array is one value, not N positional leaves
            _emit(_json_leaf(items), path, entity, entity_id, out)
            return
        for i, item in enumerate(items):
            key = stable_key(item, i)
            eid = entity_id if entity_id is not None else key
            _walk(item, f"{path}{{{key}}}", entity, eid, out, canonical)
        return

    # live object: inline at its canonical path, ref it elsewhere (also breaks cycles)
    oid = id(v)
    canon = canonical.get(oid, path)
    if canon != path:
        _emit(_ref_leaf(canon), path, entity, entity_id, out)
        return
    state = _object_state(v)
    if not state:
        _emit(_empty_leaf({}), path, entity, entity_id, out)
        return
    for k in sorted(state):
        _walk(state[k], f"{path}.{k}", entity or k, entity_id, out, canonical)


def walk_model(model) -> list[dict]:
    """Flatten a decoded hardware model into value leaves, one per data point.

    Collections are keyed by identity, scalar arrays become one json leaf, and shared
    objects are inlined at their shallowest path and become refs elsewhere.
    """
    out: list[dict] = []
    canonical = _canonical_paths(model)
    _walk(model, "model", None, None, out, canonical)
    return out


class PathCollisionError(ValueError):
    """Two leaves of one snapshot got the same path: a walker bug, not a bad file."""


def leaves_by_path(leaves: list[dict]) -> dict[str, dict]:
    """Index leaves by path, raising PathCollisionError instead of overwriting one."""
    out: dict[str, dict] = {}
    for lf in leaves:
        if lf["path"] in out:
            raise PathCollisionError(f"two values share the path {lf['path']!r}")
        out[lf["path"]] = lf
    return out


def _order(snapshots: list[dict]) -> list[dict]:
    """Sort snapshots by timestamp, then sha as a tie-break."""
    return sorted(snapshots, key=lambda s: (s["timestamp"], s["sha256"]))


def changelog(snapshots: list[dict]) -> list[dict]:
    """Collapse each path's per-snapshot values into contiguous runs.

    Each snapshot has sha256, timestamp and leaves (path -> leaf); a new run opens
    when a value changes or a path reappears. Absence is never a row.
    """
    acc = ChangelogAccumulator()
    for s in _order(snapshots):
        acc.add(s, s["leaves"])
    return acc.finalise()


class ChangelogAccumulator:
    """Streaming change-log builder, fed one snapshot at a time.

    Call add in chronological order, then finalise.
    """

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}
        self.runs: list[dict] = []

    def add(self, snap: dict, leaves: dict[str, dict]) -> None:
        """Fold one snapshot's path -> leaf mapping into the change-log."""
        # a path absent from this snapshot has ended: close its run
        for path in self._open.keys() - leaves.keys():
            self.runs.append(_finalise(self._open.pop(path), open_ended=False))
        # extend unchanged runs; (re)open on a changed value or a new path
        for path, leaf in leaves.items():
            cur = self._open.get(path)
            if cur is not None and leaf["token"] == cur["token"]:
                cur["_to"] = snap                 # same value: extend the run
                cur["n_snapshots"] += 1
            else:
                if cur is not None:
                    self.runs.append(_finalise(cur, open_ended=False))
                self._open[path] = _new_run(leaf, snap)

    def finalise(self) -> list[dict]:
        """Close every still-open run (open-ended) and return all runs."""
        for path in sorted(self._open):
            self.runs.append(_finalise(self._open.pop(path), open_ended=True))
        return self.runs

    def break_runs(self) -> None:
        """Close every open run at its last snapshot, so no run spans an undecoded one."""
        for path in sorted(self._open):
            self.runs.append(_finalise(self._open.pop(path), open_ended=False))



def _new_run(leaf: dict, snap: dict) -> dict:
    run = {k: leaf.get(k) for k in (
        "path", "path_template", "entity", "entity_id", "dtype", "enum_type",
        "token", *VALUE_COLUMNS,
    )}
    run["_from"] = snap
    run["_to"] = snap
    run["n_snapshots"] = 1
    return run


def _finalise(run: dict, open_ended: bool) -> dict:
    """Attach validity bounds; an open-ended run gets valid_to_ts=NaT."""
    run["valid_from_ts"] = run["_from"]["timestamp"]
    run["valid_from_sha"] = run["_from"]["sha256"]
    if open_ended:
        run["valid_to_ts"] = pd.NaT
        run["valid_to_sha"] = None
    else:
        run["valid_to_ts"] = run["_to"]["timestamp"]
        run["valid_to_sha"] = run["_to"]["sha256"]
    del run["_from"], run["_to"]
    return run


_VALUES_COLUMNS = [
    "path", "path_template", "entity", "entity_id", "dtype",
    *VALUE_COLUMNS,
    "valid_from_ts", "valid_to_ts", "valid_from_sha", "valid_to_sha", "n_snapshots",
]


def build_values(runs: list[dict]) -> pd.DataFrame:
    """Build the change-log table: one row per contiguous run of an unchanged value."""
    df = pd.DataFrame(runs, columns=[*_VALUES_COLUMNS, "token", "enum_type"])
    df = df.drop(columns=["token", "enum_type"])
    df["n_snapshots"] = df["n_snapshots"].astype("int32")
    return df.sort_values(["path", "valid_from_ts"]).reset_index(drop=True)


_FREQ_1Q = [("calibrate_1q", "ramsey"), ("calibrate_1q", "fine_frequency")]
_CR = [("calibrate_2q", "cross_resonance")]
_MIXER = [("mixer_calibration", "mixer_calibration")]
_WRITER_MAP: dict[str, list[tuple[str, str]]] = {  # (stage, step) writers per template (see datasheet 2)
    # CR and CRC frames follow the drive frequency of their target and control qubit
    "model.pulse_channels{Ch*.Q*.drive}.pulse_channel.frequency": _FREQ_1Q,
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance}.pulse_channel.frequency": _FREQ_1Q,
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance_cancellation}.pulse_channel.frequency": _FREQ_1Q,
    "model.pulse_channels{Ch*.Q*.drive}.pulse_channel.scale": [("calibrate_1q", "rabi")],
    "model.pulse_channels{Ch*.Q*.freq_shift}.pulse_channel.amp": [("calibrate_1q", "fine_freq_shift")],
    "model.quantum_devices{Q*}.pulse_hw_x_pi_2{amp}": [("calibrate_1q", "xpi2_amp")],
    "model.quantum_devices{Q*}.pulse_hw_x_pi_2{drag}": [("calibrate_1q", "xpi2_drag")],
    "model.quantum_devices{Q*}.mean_z_map_args": [("calibrate_1q", "zmap"), ("benchmarking", "zmap")],
    **{f"model.error_mitigation.readout_mitigation.linear{{*}}{{{cell}}}": [("calibrate_1q", "zmap")]
       for cell in ("0|0", "0|1", "1|0", "1|1")},
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance}.pulse_channel.scale": _CR,
    "model.pulse_channels{Ch*.Q*.Q*.cross_resonance_cancellation}.pulse_channel.scale": _CR,
    "model.quantum_devices{Q*}.pulse_hw_zx_pi_4{Q*}{amp}": _CR,
    "model.quantum_devices{Q*}.phase_comp_target_zx_pi_4{Q*}": _CR,
    "model.physical_channels{Ch*}.imbalance": _MIXER,
    "model.physical_channels{Ch*}.phase_offset": _MIXER,
    "model.physical_channels{Ch*}.dcbiaschannel_pair{I}._bias_value": _MIXER,
    "model.physical_channels{Ch*}.dcbiaschannel_pair{Q}._bias_value": _MIXER,
    "model.qubit_direction_couplings{direction=(*, *)}.quality": [("benchmarking", "benchmarking")],
}


def build_paths(runs: list[dict]) -> pd.DataFrame:
    """Build the path dictionary: one row per path_template.

    Records dtype, how much it moved and which routines write it (empty lists if none; see datasheet 2).
    """
    rows = []
    for template, group in _group_by(runs, "path_template").items():
        dtypes = sorted({r["dtype"] for r in group})
        enum_types = sorted({r["enum_type"] for r in group if r["enum_type"]})
        n_changes = _count_value_changes(group)
        writers = _WRITER_MAP.get(template, [])
        rows.append({
            "path_template": template,
            "entity": group[0]["entity"],
            "dtype": dtypes[0] if len(dtypes) == 1 else "mixed",
            "enum_type": enum_types[0] if len(enum_types) == 1 else (
                None if not enum_types else "mixed"),
            "n_changes": n_changes,
            "n_distinct": len({r["token"] for r in group}),
            "writer_stage": [stage for stage, _ in writers],
            "writer_measurement": [step for _, step in writers],
        })
    df = pd.DataFrame(rows)
    for col in ("n_changes", "n_distinct"):
        df[col] = df[col].astype("int32")
    return df.sort_values("path_template").reset_index(drop=True)


def _count_value_changes(runs: list[dict]) -> int:
    """Count value changes between consecutive runs of each path.

    A run split by absence or a failed decode, with the same value after, isn't a change.
    """
    n = 0
    for path_runs in _group_by(runs, "path").values():
        tokens = [r["token"] for r in sorted(path_runs, key=lambda r: r["valid_from_ts"])]
        n += sum(a != b for a, b in pairwise(tokens))
    return n


_INDEX_COLUMNS = [
    "sha256", "timestamp", "qpu_id", "server_path", "size_bytes", "loads_ok",
    "model_class",
]


def build_index(snapshots: list[dict]) -> pd.DataFrame:
    """Build the snapshot index: one row per snapshot, intrinsic facts only."""
    order = _order(snapshots)
    rows = [
        {
            "sha256": s["sha256"],
            "timestamp": s["timestamp"],
            "qpu_id": s["qpu_id"],
            "server_path": s["server_path"],
            "size_bytes": s["size_bytes"],
            "loads_ok": bool(s.get("loads_ok")),
            "model_class": s.get("model_class"),
        }
        for s in order
    ]
    return pd.DataFrame(rows, columns=_INDEX_COLUMNS).reset_index(drop=True)


def _group_by(rows: list[dict], key: str) -> dict:
    """Group rows into an insertion-ordered key -> [rows] mapping."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r[key], []).append(r)
    return groups
