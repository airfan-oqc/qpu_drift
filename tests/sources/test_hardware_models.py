import enum
from datetime import date

import pandas as pd
import pytest

from qpu_drift.sources.hardware_models.clean import (
    _WRITER_MAP,
    ChangelogAccumulator,
    PathCollisionError,
    _template,
    build_index,
    build_paths,
    build_values,
    changelog,
    classify,
    leaves_by_path,
    parse_date,
    sha256_of,
    stable_key,
    timestamp_from_path,
    walk_model,
)
from qpu_drift.sources.hardware_models.load import (
    decode_value,
    numeric_values,
    snapshot_state,
    value_as_of,
    values_at,
)


class Reference(enum.Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class Baseband:
    def __init__(self, ref):
        self._reference = ref


class PulseChannel:
    def __init__(self, frequency, scale):
        self.frequency = frequency
        self.scale = scale


class Qubit:
    def __init__(self, index, frequency, **extra):
        self.index = index
        self.frequency = frequency
        for k, v in extra.items():
            setattr(self, k, v)


class Model:
    pass


def make_model(q1_freq=5.1e9):
    """Build a tiny live model; the drive channel is shared by pulse_channels and q1."""
    shared = PulseChannel(frequency=5.0e9, scale=1 + 2j)
    m = Model()
    m.default_repeat_count = 1000
    m.default_repetition_period = 0.0005
    m.baseband = Baseband(Reference.INTERNAL)
    m.pulse_channels = {"Ch1.Q1.drive": shared}
    m.qubits = [
        Qubit(1, q1_freq, drive=shared),   # shared -> becomes a ref leaf
        Qubit(2, 5.2e9, tags=[]),          # empty list -> present-but-empty
    ]
    m.weights = [0.1, 0.2, 0.3]            # scalar array -> one json leaf
    m.extras = {}                          # empty dict -> present-but-empty
    return m, shared


def _by_path(model):
    return {lf["path"]: lf for lf in walk_model(model)}


def test_sha256_of_is_deterministic_and_bytewise():
    assert sha256_of(b"abc") == sha256_of(b"abc")
    assert sha256_of(b"abc") != sha256_of(b"abd")


def test_timestamp_from_path_parses_dated_path_left_naive():
    ts = timestamp_from_path("2026/08/18/09-13-31_calibration.gz")
    assert ts == pd.Timestamp("2026-08-18 09:13:31")
    assert ts.tz is None


def test_timestamp_from_path_rejects_undated_names():
    assert timestamp_from_path("calibration_backup.gz") is None
    assert timestamp_from_path("2025/01/01/notes.txt") is None


def test_parse_date_handles_iso_and_none():
    assert parse_date("2025-01-01") == date(2025, 1, 1)
    assert parse_date(None) is None


def _sole_value(rec):
    """Return the single populated value_* column, checking the tagged-union invariant."""
    cols = ("value_int", "value_float", "value_bool", "value_str", "value_json")
    populated = [c for c in cols if rec[c] is not None]
    assert len(populated) <= 1, f"{rec['dtype']} set >1 value column: {populated}"
    return (populated[0], rec[populated[0]]) if populated else (None, None)


def test_classify_covers_each_scalar_dtype():
    assert classify(3)["dtype"] == "int"
    assert classify(3.5)["dtype"] == "float"
    assert classify(True)["dtype"] == "bool"          # bool before int
    assert classify("x")["dtype"] == "str"
    assert classify(None)["dtype"] == "null"
    assert classify(1 + 2j)["dtype"] == "complex"


def test_classify_bool_is_not_int():
    rec = classify(True)
    assert rec["value_bool"] is True
    assert rec["value_int"] is None


def test_classify_enum_keeps_name_and_type():
    rec = classify(Reference.EXTERNAL)
    assert rec["dtype"] == "enum"
    assert rec["value_str"] == "EXTERNAL"
    assert rec["enum_type"] == "Reference"


def test_classify_complex_stores_re_im_as_json():
    col, val = _sole_value(classify(1 + 2j))
    assert col == "value_json"
    assert val == "[1.0,2.0]"


def test_classify_null_populates_no_value_column():
    col, _ = _sole_value(classify(None))
    assert col is None


def test_classify_float_token_is_bitwise_nan_safe():
    # equal bit patterns share a token so a run never fragments; distinct differ
    assert classify(float("nan"))["token"] == classify(float("nan"))["token"]
    assert classify(0.0)["token"] != classify(-0.0)["token"]


def test_classify_rejects_non_leaf():
    with pytest.raises(TypeError):
        classify([1, 2, 3])


def test_stable_key_prefers_identity_then_falls_back_to_index():
    assert stable_key(Qubit(7, 5.0e9), 3) == "index=7"
    assert stable_key(object(), 3) == "[3]"


def test_walk_keys_collections_by_identity_not_position():
    leaves = _by_path(make_model()[0])
    assert "model.qubits{index=1}.frequency" in leaves
    assert "model.pulse_channels{Ch1.Q1.drive}.scale" in leaves
    # nothing keyed positionally
    assert not any("[" in p for p in leaves)


def test_walk_collapses_scalar_array_to_one_json_leaf():
    leaves = _by_path(make_model()[0])
    weights = leaves["model.weights"]
    assert weights["dtype"] == "json"
    assert weights["value_json"] == "[0.1,0.2,0.3]"


def test_walk_marks_empty_containers_present_but_empty():
    leaves = _by_path(make_model()[0])
    assert leaves["model.extras"]["dtype"] == "empty"
    assert leaves["model.extras"]["value_json"] == "{}"
    assert leaves["model.qubits{index=2}.tags"]["value_json"] == "[]"


def test_walk_emits_shared_object_as_ref_to_canonical_path():
    leaves = _by_path(make_model()[0])
    drive = leaves["model.qubits{index=1}.drive"]
    assert drive["dtype"] == "ref"
    # points at where the object was first walked (pulse_channels sorts first)
    assert drive["value_str"] == "model.pulse_channels{Ch1.Q1.drive}"
    # ...and the shared object's own leaves are only emitted once
    assert "model.pulse_channels{Ch1.Q1.drive}.scale" in leaves
    assert "model.qubits{index=1}.drive.scale" not in leaves


def test_walk_template_wildcards_identity_segments():
    leaves = _by_path(make_model()[0])
    assert leaves["model.qubits{index=1}.frequency"]["path_template"] == (
        "model.qubits{index=*}.frequency"
    )
    assert leaves["model.pulse_channels{Ch1.Q1.drive}.scale"]["path_template"] == (
        "model.pulse_channels{Ch*.Q*.drive}.scale"
    )


@pytest.mark.parametrize("path, template", [
    ("model.quantum_devices{Q4}.pulse_hw_x_pi_2{drag}", "model.quantum_devices{Q*}.pulse_hw_x_pi_2{drag}"),
    ("model.quantum_devices{R12}.id", "model.quantum_devices{R*}.id"),
    ("model.quantum_devices{Q2}.pulse_hw_zx_pi_4{Q10}{amp}", "model.quantum_devices{Q*}.pulse_hw_zx_pi_4{Q*}{amp}"),
    ("model.error_mitigation.readout_mitigation.linear{11}{0|1}",
     "model.error_mitigation.readout_mitigation.linear{*}{0|1}"),
    ("model.physical_channels{Ch3}.dcbiaschannel_pair{I}._bias_value",
     "model.physical_channels{Ch*}.dcbiaschannel_pair{I}._bias_value"),
    ("model.qubit_direction_couplings{direction=(1, 10)}.quality",
     "model.qubit_direction_couplings{direction=(*, *)}.quality"),
    ("model.things[3].x", "model.things[*].x"),
])
def test_template_removes_ids_but_keeps_kinds_roles_and_fields(path, template):
    assert _template(path) == template


def test_writer_map_keys_are_templates():
    assert all(_template(k) == k for k in _WRITER_MAP)


def test_build_paths_lists_every_writer_in_matching_order():
    def run(template):
        return {"path": template.replace("*", "1"), "path_template": template, "entity": "quantum_devices",
                "dtype": "float", "enum_type": None, "token": "t", "valid_from_ts": pd.Timestamp("2025-01-01")}
    zmap = "model.quantum_devices{Q*}.mean_z_map_args"
    paths = build_paths([run(zmap), run("model.quantum_devices{Q*}.id")]).set_index("path_template")
    assert list(paths.at[zmap, "writer_stage"]) == ["calibrate_1q", "benchmarking"]
    assert list(paths.at[zmap, "writer_measurement"]) == ["zmap", "zmap"]
    assert list(paths.at["model.quantum_devices{Q*}.id", "writer_stage"]) == []


def test_walk_carries_entity_and_entity_id():
    leaves = _by_path(make_model()[0])
    scale = leaves["model.pulse_channels{Ch1.Q1.drive}.scale"]
    assert scale["entity"] == "pulse_channels"
    assert scale["entity_id"] == "Ch1.Q1.drive"
    assert leaves["model.default_repeat_count"]["entity_id"] is None


class ShadowModel:
    """Model whose class property hides a stale instance attribute."""

    @property
    def devices(self):
        return list(self.device_map.values())


def test_walk_skips_instance_state_hidden_by_a_property():
    m = ShadowModel()
    m.device_map = {"Q1": Qubit(1, 5.0e9)}
    m.__dict__["devices"] = [Qubit(1, 4.0e9)]      # stale copy, never visible
    leaves = _by_path(m)
    assert leaves["model.device_map{Q1}.frequency"]["value_float"] == 5.0e9
    assert not any(p.startswith("model.devices") for p in leaves)


def _leaf(path, value):
    rec = classify(value)
    rec.update(path=path, path_template=path, entity="e", entity_id=None)
    return rec


def _snap(sha, ts, leaves):
    return {
        "sha256": sha, "timestamp": pd.Timestamp(ts),
        "leaves": {lf["path"]: lf for lf in leaves},
    }


def test_changelog_collapses_a_constant_value_into_one_open_run():
    snaps = [
        _snap("a", "2025-01-01", [_leaf("p", 5.0)]),
        _snap("b", "2025-01-02", [_leaf("p", 5.0)]),
        _snap("c", "2025-01-03", [_leaf("p", 5.0)]),
    ]
    runs = changelog(snaps)
    assert len(runs) == 1
    (run,) = runs
    assert run["n_snapshots"] == 3
    assert run["valid_from_ts"] == pd.Timestamp("2025-01-01")
    assert pd.isna(run["valid_to_ts"])          # still holds at the latest snapshot


def test_changelog_splits_on_value_change():
    snaps = [
        _snap("a", "2025-01-01", [_leaf("p", 5.0)]),
        _snap("b", "2025-01-02", [_leaf("p", 5.0)]),
        _snap("c", "2025-01-03", [_leaf("p", 6.0)]),
    ]
    runs = sorted(changelog(snaps), key=lambda r: r["valid_from_ts"])
    assert [r["n_snapshots"] for r in runs] == [2, 1]
    assert runs[0]["valid_to_ts"] == pd.Timestamp("2025-01-02")   # closed at last hold
    assert pd.isna(runs[1]["valid_to_ts"])


def test_changelog_absence_closes_a_run_and_is_not_a_row():
    snaps = [
        _snap("a", "2025-01-01", [_leaf("p", 5.0)]),
        _snap("b", "2025-01-02", []),                 # p absent
        _snap("c", "2025-01-03", [_leaf("p", 5.0)]),
    ]
    runs = sorted(changelog(snaps), key=lambda r: r["valid_from_ts"])
    assert len(runs) == 2                              # absence breaks the run
    assert runs[0]["valid_to_ts"] == pd.Timestamp("2025-01-01")


def test_changelog_present_null_is_a_run_distinct_from_absence():
    snaps = [
        _snap("a", "2025-01-01", [_leaf("p", None)]),
        _snap("b", "2025-01-02", [_leaf("p", None)]),
    ]
    runs = changelog(snaps)
    assert len(runs) == 1
    assert runs[0]["dtype"] == "null"
    assert runs[0]["n_snapshots"] == 2


def test_leaves_by_path_raises_on_a_path_collision():
    with pytest.raises(PathCollisionError):
        leaves_by_path([_leaf("p", 1.0), _leaf("p", 2.0)])
    assert set(leaves_by_path([_leaf("p", 1.0), _leaf("q", 2.0)])) == {"p", "q"}


def _break_snapshots():
    """Return runs for a, a failed snapshot b, then c, with p unchanged throughout."""
    a = _snap("a", "2025-01-01", [_leaf("p", 5.0)])
    c = _snap("c", "2025-01-03", [_leaf("p", 5.0)])
    acc = ChangelogAccumulator()
    acc.add(a, a["leaves"])
    acc.break_runs()                    # b failed to decode
    acc.add(c, c["leaves"])
    return acc.finalise()


def test_break_runs_stops_a_run_spanning_a_failed_snapshot():
    runs = sorted(_break_snapshots(), key=lambda r: r["valid_from_ts"])
    assert len(runs) == 2
    assert runs[0]["valid_to_ts"] == pd.Timestamp("2025-01-01")
    assert runs[1]["valid_from_ts"] == pd.Timestamp("2025-01-03")


def test_build_paths_does_not_count_a_split_with_the_same_value_as_a_change():
    paths = build_paths(_break_snapshots())
    assert paths.loc[paths["path_template"] == "p", "n_changes"].iloc[0] == 0


def _two_day_snapshots():
    """Return two snapshots where only q1's frequency changes."""
    m0, _ = make_model(q1_freq=5.1e9)
    m1, _ = make_model(q1_freq=5.15e9)
    return [
        {"sha256": "s0", "timestamp": pd.Timestamp("2025-01-01 08:00:00"),
         "qpu_id": "qpu:test", "server_path": "/a/2025/01/01/08-00-00_calibration.gz",
         "size_bytes": 10, "loads_ok": True, "model_class": "Model",
         "leaves": _by_path(m0)},
        {"sha256": "s1", "timestamp": pd.Timestamp("2025-01-02 08:00:00"),
         "qpu_id": "qpu:test", "server_path": "/a/2025/01/02/08-00-00_calibration.gz",
         "size_bytes": 11, "loads_ok": True, "model_class": "Model",
         "leaves": _by_path(m1)},
    ]


def test_build_values_drops_internal_columns_and_types_count():
    runs = changelog(_two_day_snapshots())
    values = build_values(runs)
    assert "token" not in values.columns
    assert "enum_type" not in values.columns
    assert values["n_snapshots"].dtype == "int32"
    # the changed frequency yields two runs for that path
    freq = values[values["path"] == "model.qubits{index=1}.frequency"]
    assert len(freq) == 2


def test_build_paths_is_one_row_per_template_with_observed_dynamics():
    runs = changelog(_two_day_snapshots())
    paths = build_paths(runs)
    assert paths["path_template"].is_unique
    row = paths[paths["path_template"] == "model.qubits{index=*}.frequency"].iloc[0]
    # q1 changed, q2 did not: one change across the template
    assert row["n_changes"] == 1
    assert (row["n_changes"] > 0)
    ref_row = paths[paths["path_template"] == "model.baseband._reference"].iloc[0]
    assert ref_row["dtype"] == "enum"
    assert ref_row["enum_type"] == "Reference"


def test_build_index_one_row_per_snapshot_intrinsic_fields_only():
    index = build_index(_two_day_snapshots())
    assert len(index) == 2
    assert list(index.columns) == [
        "sha256", "timestamp", "qpu_id", "server_path", "size_bytes",
        "loads_ok", "model_class",
    ]
    # derived/inferred columns are intentionally absent from the clean layer
    for col in ("n_leaves", "n_changed", "day_seq", "stage_inferred"):
        assert col not in index.columns


def test_decode_value_round_trips_each_dtype():
    assert decode_value(pd.Series(classify(3))) == 3
    assert decode_value(pd.Series(classify(3.5))) == 3.5
    assert decode_value(pd.Series(classify(True))) is True
    assert decode_value(pd.Series(classify("x"))) == "x"
    assert decode_value(pd.Series(classify(None))) is None
    assert decode_value(pd.Series(classify(1 + 2j))) == (1 + 2j)
    assert decode_value(pd.Series(classify(Reference.INTERNAL))) == "INTERNAL"


def test_value_as_of_reads_the_snapshot_in_force_at_the_timestamp():
    snaps = _two_day_snapshots()
    values, index = build_values(changelog(snaps)), build_index(snaps)
    path = "model.qubits{index=1}.frequency"
    assert value_as_of(values, path, "2025-01-01 12:00:00", index) == 5.1e9
    assert value_as_of(values, path, "2025-01-02 12:00:00", index) == 5.15e9  # open run
    with pytest.raises(KeyError):
        value_as_of(values, path, "2024-12-31", index)                        # before first
    with pytest.raises(KeyError):
        value_as_of(values, "model.nonexistent", "2025-01-02", index)


def test_value_as_of_returns_none_only_for_a_stored_null():
    snaps = [_snap("aaa1", "2025-01-01 08:00:00", [_leaf("p", None)])]
    values = build_values(changelog(snaps))
    index = pd.DataFrame({"sha256": ["aaa1"], "timestamp": [pd.Timestamp("2025-01-01 08:00:00")]})
    assert value_as_of(values, "p", "2025-01-02", index) is None
    with pytest.raises(KeyError):
        value_as_of(values, "q", "2025-01-02", index)


def test_value_as_of_does_not_resurrect_a_dropped_path():
    values = build_values(changelog(_flicker_snapshots()))
    index = _flicker_index()
    assert value_as_of(values, "p", "2025-01-01 20:00:00", index) == 5.0
    with pytest.raises(KeyError):
        value_as_of(values, "p", "2025-01-02 20:00:00", index)  # absent at s1
    assert value_as_of(values, "p", "2025-01-03 20:00:00", index) == 6.0


def _flicker_snapshots():
    """Return three snapshots where p is present, absent, then back changed; q is constant."""
    return [
        _snap("aaa1", "2025-01-01 08:00:00", [_leaf("p", 5.0), _leaf("q", 1.0)]),
        _snap("bbb2", "2025-01-02 08:00:00", [_leaf("q", 1.0)]),
        _snap("ccc3", "2025-01-03 08:00:00", [_leaf("p", 6.0), _leaf("q", 1.0)]),
    ]


def _flicker_index():
    return pd.DataFrame(
        {
            "sha256": ["aaa1", "bbb2", "ccc3"],
            "timestamp": pd.to_datetime(
                ["2025-01-01 08:00:00", "2025-01-02 08:00:00", "2025-01-03 08:00:00"]
            ),
        }
    )


def test_snapshot_state_by_sha_reconstructs_exact_snapshot():
    values = build_values(changelog(_flicker_snapshots()))
    state = snapshot_state(values, sha="aaa1", index=_flicker_index())
    assert dict(zip(state["path"], state["value"])) == {"p": 5.0, "q": 1.0}


def test_snapshot_state_by_sha_omits_paths_absent_in_that_snapshot():
    values = build_values(changelog(_flicker_snapshots()))
    state = snapshot_state(values, sha="bbb2", index=_flicker_index())
    # p was absent at bbb2, so it must not come back from its earlier value
    assert list(state["path"]) == ["q"]
    assert state["value"].tolist() == [1.0]


def test_snapshot_state_by_sha_accepts_a_unique_prefix():
    values = build_values(changelog(_flicker_snapshots()))
    state = snapshot_state(values, sha="ccc", index=_flicker_index())
    assert dict(zip(state["path"], state["value"])) == {"p": 6.0, "q": 1.0}


def test_helpers_refuse_a_snapshot_that_failed_to_decode():
    values = build_values(changelog(_flicker_snapshots()))
    index = _flicker_index().assign(loads_ok=[True, False, True])
    with pytest.raises(ValueError):
        snapshot_state(values, sha="bbb2", index=index)
    with pytest.raises(ValueError):
        value_as_of(values, "q", "2025-01-02 20:00:00", index)
    assert value_as_of(values, "q", "2025-01-03 20:00:00", index) == 1.0


def test_snapshot_state_by_timestamp_is_the_snapshot_in_force():
    values = build_values(changelog(_flicker_snapshots()))
    # between s0 and s1 the device is s0; after s1, p has been dropped
    state = snapshot_state(values, timestamp="2025-01-01 20:00:00", index=_flicker_index())
    assert dict(zip(state["path"], state["value"])) == {"p": 5.0, "q": 1.0}
    state = snapshot_state(values, timestamp="2025-01-02 20:00:00", index=_flicker_index())
    assert dict(zip(state["path"], state["value"])) == {"q": 1.0}


def test_snapshot_state_keeps_raw_columns_when_decode_is_off():
    values = build_values(changelog(_flicker_snapshots()))
    state = snapshot_state(values, sha="aaa1", index=_flicker_index(), decode=False)
    assert "value" not in state.columns
    assert set(state["path"]) == {"p", "q"}
    assert state.loc[state["path"] == "p", "value_float"].iloc[0] == 5.0


def test_snapshot_state_decodes_complex_and_enum_operating_points():
    values = build_values(changelog(_two_day_snapshots()))
    state = snapshot_state(values, sha="s0", index=build_index(_two_day_snapshots()))
    by_path = dict(zip(state["path"], state["value"]))
    assert by_path["model.pulse_channels{Ch1.Q1.drive}.scale"] == (1 + 2j)
    assert by_path["model.baseband._reference"] == "INTERNAL"


def test_snapshot_state_requires_exactly_one_selector():
    values = build_values(changelog(_flicker_snapshots()))
    with pytest.raises(ValueError):
        snapshot_state(values)
    with pytest.raises(ValueError):
        snapshot_state(values, sha="aaa1", timestamp="2025-01-01")


def test_snapshot_state_raises_on_unknown_sha():
    values = build_values(changelog(_flicker_snapshots()))
    with pytest.raises(KeyError):
        snapshot_state(values, sha="zzz9", index=_flicker_index())


def test_snapshot_state_empty_before_first_snapshot():
    values = build_values(changelog(_flicker_snapshots()))
    state = snapshot_state(values, timestamp="2024-12-31", index=_flicker_index())
    assert len(state) == 0
    assert "path" in state.columns          # columns preserved on empty result


def test_values_at_matches_keys_and_aligns_to_the_request():
    series = pd.DataFrame({
        "qubit": [1, 1, 2],
        "valid_from_ts": pd.to_datetime(["2025-01-01", "2025-01-03", "2025-01-01"]),
        "valid_to_ts": pd.to_datetime(["2025-01-02", None, None]),  # qubit 1 absent on 01-02 to 01-03
        "scale": [0.1, 0.2, 0.5],
    })
    at = pd.DataFrame({
        "t": pd.to_datetime(["2025-01-04 00:00", "2025-01-02 12:00", None, "2025-01-01 00:00",
                             "2024-12-31 00:00"]),
        "qubit": [1, 1, 1, 2, 2],
    }, index=[10, 11, 12, 13, 14])
    got = values_at(at, series, ["qubit"], col="scale")
    assert got.index.tolist() == [10, 11, 12, 13, 14]
    assert got.loc[10] == 0.2 and got.loc[13] == 0.5
    assert got.loc[[11, 12, 14]].isna().all()  # absent, no time, before the first run


def test_numeric_values_reads_float_int_and_complex():
    values = pd.DataFrame({
        "dtype": ["float", "int", "complex", "str"],
        "value_float": [0.5, None, None, None],
        "value_int": [None, 3, None, None],
        "value_json": [None, None, "[1.0, -2.0]", None],
    })
    got = numeric_values(values)
    assert got.iloc[:3].tolist() == [0.5, 3, complex(1, -2)]
    assert pd.isna(got.iloc[3])

