import gzip
import io
import json

import numpy as np
import pytest

from qpu_drift import decode
from qpu_drift.decode import (
    Fit,
    PayloadError,
    Result,
    array,
    extract,
    extract_generic,
    iter_targets,
    payload,
    read_fits,
    read_header,
    register_extractor,
    results_frame,
)


def _numpyarray_node(values: list[float]) -> dict:
    """Build a numpyarray node the way payloads store float64 arrays."""
    buf = np.asarray(values, dtype="<f8").tobytes()
    return {
        "type": "numpyarray",
        "dtype": "<f8",
        "shape": [len(values)],
        "value": buf.hex(),
    }


def _ramsey_payload() -> dict:
    """Return a Ramsey run over Q1 and Q4 (old_frequencies -> new_frequency)."""
    return {
        "type": "Result",
        "name": "Ramsey",
        "dict": {
            "id": 256384,
            "measurement_name": "ramsey_group1",
            "time_start": "2024-09-18 11:53:21.161967",
            "time_end": "2024-09-18 11:53:21.601072",
            "config": {
                "type": "Config",
                "dict": {
                    "shots": 1000,
                    "old_frequencies": {"Q1": 4.9e9, "Q4": 5.1e9},
                },
            },
            "data": {
                "Q1": {
                    "type": "Data",
                    "dict": {
                        "raw_output": _numpyarray_node([0.1, 0.2, 0.3]),
                        "fit": {
                            "type": "FittingBase",
                            "dict": {"model": "cos", "r2": 0.99, "params": [1, 2]},
                        },
                        "new_frequency": 4.9001e9,
                    },
                },
                "Q4": {
                    "type": "Data",
                    "dict": {
                        "fit": {
                            "type": "FittingBase",
                            "dict": {"model": "cos", "r2": 0.95},
                        },
                        "new_frequency": 5.1002e9,
                    },
                },
            },
        },
    }


def _coupling_payload() -> dict:
    """Return a two-qubit run keyed by a directed coupling (plural old_amplitudes)."""
    return {
        "type": "Result",
        "name": "CRUnitaryIZ",
        "dict": {
            "id": 999,
            "measurement_name": "cr_u_C4-T5",
            "config": {"type": "Config", "dict": {"old_amplitudes": {"C4-T5": 0.20}}},
            "data": {
                "C4-T5": {
                    "type": "Data",
                    "dict": {
                        "fit": {"type": "FittingBase", "dict": {"r2": 0.88}},
                        "new_amplitude": 0.22,
                    },
                }
            },
        },
    }


def _gzip_bytes(obj: dict) -> bytes:
    return gzip.compress(json.dumps(obj).encode("utf-8"))


def test_array_roundtrips_float64():
    node = _numpyarray_node([1.5, -2.0, 3.25])
    out = array(node)
    assert out.dtype == np.dtype("<f8")
    np.testing.assert_array_equal(out, np.array([1.5, -2.0, 3.25]))


def test_array_honours_shape_and_is_writable():
    values = [1.0, 2.0, 3.0, 4.0]
    node = {
        "type": "numpyarray",
        "dtype": "<f8",
        "shape": [2, 2],
        "value": np.asarray(values, dtype="<f8").tobytes().hex(),
    }
    out = array(node)
    assert out.shape == (2, 2)
    out[0, 0] = 9.0  # frombuffer views are read-only, so this checks array() copies
    assert out[0, 0] == 9.0


def test_array_rejects_non_array_node():
    with pytest.raises(PayloadError):
        array({"type": "Data", "dict": {}})


def test_array_rejects_malformed_hex():
    with pytest.raises(PayloadError):
        array({"type": "numpyarray", "dtype": "<f8", "value": "zzzz"})


def test_array_reads_legacy_list_form():
    row = lambda *v: {"type": "numpyarray", "list": list(v)}
    f32 = {"type": "numpyfloat32", "float": 0.5}
    out = array({"type": "numpyarray", "list": [row(1.0, f32), row({"type": "numpyint64", "int": 3}, 4.0)]})
    np.testing.assert_array_equal(out, [[1.0, 0.5], [3.0, 4.0]])
    z = array({"type": "numpyarray", "list": [{"type": "complex", "real": 1.0, "imag": -2.0}]})
    assert z.dtype.kind == "c" and z[0] == complex(1, -2)


def test_array_rejects_ragged_list_form():
    with pytest.raises(PayloadError):
        array({"type": "numpyarray", "list": [[1.0, 2.0], [3.0]]})


def test_payload_from_path(tmp_path):
    path = tmp_path / "11-53-21_ramsey.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(_ramsey_payload(), fh)
    obj = payload(path)
    assert obj["name"] == "Ramsey"
    assert obj["dict"]["id"] == 256384


def test_payload_from_bytes_and_filelike():
    raw = _gzip_bytes(_ramsey_payload())
    assert payload(raw)["name"] == "Ramsey"
    assert payload(io.BytesIO(raw))["name"] == "Ramsey"


def test_payload_rejects_non_gzip():
    with pytest.raises(PayloadError):
        payload(b"not gzip at all")


def test_payload_rejects_non_envelope():
    # valid gzip + JSON, but not a result envelope (no 'dict' node)
    raw = gzip.compress(json.dumps({"hello": "world"}).encode())
    with pytest.raises(PayloadError):
        payload(raw)


def test_read_header_pulls_index_matching_fields():
    h = read_header(_ramsey_payload())
    assert (h.id, h.measurement_type, h.measurement_name) == (
        256384, "Ramsey", "ramsey_group1",
    )
    assert h.time_start.startswith("2024-09-18")


def test_iter_targets_unwraps_each_data_node():
    targets = dict(iter_targets(_ramsey_payload()))
    assert set(targets) == {"Q1", "Q4"}
    assert "new_frequency" in targets["Q1"]


def test_read_fits_finds_fit_leaves():
    _, q1 = next(iter_targets(_ramsey_payload()))
    fits = read_fits(q1)
    assert fits == [Fit(model="cos", r2=0.99, params=[1, 2], covariance=None)]


def test_read_fits_reads_both_maac_fit_formats():
    modern = {
        "type": "FittingBase", "name": "Exponential",
        "dict": {
            "function_string": "y = A*exp(-x/x0) + B",
            "optimised_results": _numpyarray_node([1.0, 2.0, 3.0]),
            "covariance_matrix": [[0.1]],
            "r2": 0.97,
        },
    }
    legacy = {
        "type": "Fit",
        "dict": {"function_string": "y = A*x + B", "opt_vector": [4.0, 5.0], "cov_matrix": [[0.2]]},
    }
    fits = read_fits({"raw_output": None, "fit": modern, "fits": [legacy, legacy]})
    assert [f.model for f in fits] == ["y = A*exp(-x/x0) + B", "y = A*x + B", "y = A*x + B"]
    assert [f.r2 for f in fits] == [0.97, None, None]  # the older format stores no r2
    np.testing.assert_array_equal(fits[0].params, [1.0, 2.0, 3.0])
    assert fits[1].params == [4.0, 5.0] and fits[1].covariance == [[0.2]]


def test_extract_generic_pairs_old_new_and_r2():
    rows = extract_generic(_ramsey_payload())
    by_target = {r.target: r for r in rows}
    assert by_target["Q1"] == Result(
        id=256384, measurement_type="Ramsey", target="Q1", param="frequency",
        old=4.9e9, new=4.9001e9, r2=0.99, fit_model="cos",
    )
    assert by_target["Q4"].old == 5.1e9 and by_target["Q4"].r2 == 0.95


def test_extract_generic_handles_plural_coupling_config():
    (row,) = extract_generic(_coupling_payload())
    assert (row.target, row.param, row.old, row.new) == ("C4-T5", "amplitude", 0.20, 0.22)


def test_extract_generic_row_for_verification_type_without_new():
    payload = {
        "type": "Result", "name": "MeasureBands",
        "dict": {
            "id": 1, "config": {"dict": {}},
            "data": {"Q1": {"type": "Data", "dict": {
                "bands": {"type": "FittingBase", "dict": {"r2": 0.7}},
            }}},
        },
    }
    (row,) = extract_generic(payload)
    assert row.param is None and row.new is None and row.r2 == 0.7


def test_extract_dispatches_to_registered_extractor():
    sentinel = [Result(1, "Custom", "Q9", "thing", 0, 1, None, None)]

    @register_extractor("Custom")
    def _custom(_obj):
        return sentinel

    try:
        assert extract({"name": "Custom", "dict": {}}) is sentinel
        # unregistered types still fall back to the generic reader
        assert extract(_coupling_payload())[0].measurement_type == "CRUnitaryIZ"
    finally:
        decode._EXTRACTORS.pop("Custom", None)


def test_results_frame_builds_tidy_table():
    df = results_frame([_ramsey_payload(), _coupling_payload()])
    assert list(df.columns) == [
        "id", "measurement_type", "target", "param", "old", "new", "r2",
        "fit_model", "error",
    ]
    assert len(df) == 3  # Q1, Q4, C4-T5
    assert set(df["id"]) == {256384, 999}


def test_results_frame_empty_is_typed():
    df = results_frame([])
    assert len(df) == 0
    assert "fit_model" in df.columns


def _t1_payload() -> dict:
    """Return a T1 run with bare T1 and T1_err leaves and a fit with r2."""
    return {
        "type": "Result", "name": "T1",
        "dict": {
            "id": 263692, "measurement_name": "t1_group1",
            "data": {
                "Q1": {"type": "Data", "dict": {
                    "raw_output": _numpyarray_node([0.1, 0.2, 0.3]),
                    "fit": {"type": "FittingBase", "dict": {"r2": 0.9847}},
                    "T1": 9.26e-05, "T1_err": 1.18e-05,
                }},
                "Q4": {"type": "Data", "dict": {
                    "fit": {"type": "FittingBase", "dict": {"r2": 0.99}},
                    "T1": 6.08e-05, "T1_err": 1.0e-05,
                }},
            },
        },
    }


def _t2echo_payload() -> dict:
    return {
        "type": "Result", "name": "T2echo",
        "dict": {"id": 263694, "data": {"Q1": {"type": "Data", "dict": {
            "fit": {"type": "FittingBase", "dict": {"r2": 0.90}},
            "T2": 1.64e-05, "T2_err": 1.48e-06,
        }}}},
    }


def _rb1q_payload() -> dict:
    """Return an RB1q run with fidelity and its uncertainty, and a fit with no r2."""
    return {
        "type": "Result", "name": "RandomizedBenchmarking1q",
        "dict": {"id": 263696, "data": {"Q1": {"type": "Data", "dict": {
            "fit": {"type": "Fit", "dict": {"function_string": "A*p**m+B", "opt_vector": [1, 2]}},
            "fidelity": 0.9968, "fidelity_uncertainty": 0.00025,
        }}}},
    }


def _rb2q_payload() -> dict:
    return {
        "type": "Result", "name": "RandomizedBenchmarking2q",
        "dict": {"id": 263700, "data": {"C27-T34": {"type": "Data", "dict": {
            "fit": {"type": "Fit", "dict": {"function_string": "A*p**m+B"}},
            "fidelity": 0.9719, "fidelity_uncertainty": 0.00118,
            "fidelity_clifford": 0.9583, "fidelity_clifford_uncertainty": 0.00174,
        }}}},
    }


def _zmap_payload() -> dict:
    """Return a ZMap (fRO) run with a bare fidelity leaf, no fit and no uncertainty."""
    return {
        "type": "Result", "name": "ZMap",
        "dict": {"id": 263698, "data": {"Q1": {"type": "Data", "dict": {
            "fidelity": 0.9616, "g_fidelity": 0.9774, "e_fidelity": 0.9458,
        }}}},
    }


def test_t1_extractor_reads_value_error_and_r2():
    rows = {r.target: r for r in extract(_t1_payload())}
    assert set(rows) == {"Q1", "Q4"}
    q1 = rows["Q1"]
    assert (q1.measurement_type, q1.param) == ("T1", "T1")
    assert q1.new == 9.26e-05 and q1.error == 1.18e-05 and q1.r2 == 0.9847
    assert q1.old is None


def test_t2echo_extractor_maps_t2_fields():
    (row,) = extract(_t2echo_payload())
    assert (row.param, row.new, row.error, row.r2) == ("T2", 1.64e-05, 1.48e-06, 0.90)


def test_rb_extractors_read_fidelity_without_scalar_r2():
    (rb1,) = extract(_rb1q_payload())
    assert (rb1.param, rb1.new, rb1.error, rb1.r2) == ("fidelity", 0.9968, 0.00025, None)
    (rb2,) = extract(_rb2q_payload())
    assert rb2.target == "C27-T34" and rb2.new == 0.9719 and rb2.r2 is None


def test_zmap_extractor_value_only():
    (row,) = extract(_zmap_payload())
    assert (row.param, row.new, row.error, row.r2) == ("fidelity", 0.9616, None, None)


def _int_node(i: int) -> dict:
    return {"type": "numpyint64", "int": i}


def _rb_payload(n_seeds: int) -> dict:
    """Return an RB run with typed header times and a large list-valued seed_array."""
    return {
        "type": "Result",
        "name": "RandomizedBenchmarking1q",
        "dict": {
            "time_start": {"type": "datetime", "string": "2025-10-21 22:08:25"},
            "time_end": {"type": "datetime", "string": "2025-10-21 22:10:48"},
            "id": 258996,
            "measurement_name": "rb1q_group1",
            "config": {
                "type": "Config",
                "name": "Config",
                "dict": {
                    "shots": 1000,
                    "lengths": {"type": "numpyarray", "list": [_int_node(1), _int_node(10)]},
                    "seed_array": {"type": "numpyarray",
                                   "list": [[_int_node(i), _int_node(i + 1)] for i in range(n_seeds)]},
                    "old_scale_factors": {"Q1": {"type": "complex", "real": 0.5, "imag": -0.1}},
                    "pulse_shape": {"type": "pulseshape", "name": "SQUARE"},
                    "raw": _numpyarray_node([1.0, 2.0]),
                },
            },
            "data": {"Q1": {"type": "Data", "dict": {"fidelity": 0.99}}},
        },
    }


def test_plain_converts_typed_nodes():
    cfg = decode.plain(_rb_payload(2)["dict"]["config"])
    np.testing.assert_array_equal(cfg["lengths"], [1, 10])
    np.testing.assert_array_equal(cfg["seed_array"], [[0, 1], [1, 2]])
    assert cfg["old_scale_factors"] == {"Q1": complex(0.5, -0.1)}
    assert cfg["pulse_shape"] == "SQUARE"
    np.testing.assert_array_equal(cfg["raw"], [1.0, 2.0])
    assert decode.plain({"_fit_type": {"type": "fittype", "name": "EXPONENTIAL"}}) == {"_fit_type": "EXPONENTIAL"}


def test_read_config_reads_header_and_config(tmp_path):
    path = tmp_path / "rb.gz"
    path.write_bytes(_gzip_bytes(_rb_payload(20000)))  # config larger than one read chunk
    header, cfg = decode.read_config(path, chunk=256)
    assert header == decode.PayloadHeader(258996, "rb1q_group1", "RandomizedBenchmarking1q",
                                          "2025-10-21 22:08:25", "2025-10-21 22:10:48")
    assert cfg["shots"] == 1000 and len(cfg["seed_array"]) == 20000


def test_read_config_rejects_missing_config(tmp_path):
    obj = _rb_payload(1)
    del obj["dict"]["config"]
    path = tmp_path / "noconfig.gz"
    path.write_bytes(_gzip_bytes(obj))
    with pytest.raises(PayloadError):
        decode.read_config(path)
    bad = tmp_path / "bad.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(PayloadError):
        decode.read_config(bad)


def _old_line(slope: float, intercept: float, cov: list[list[float]]) -> dict:
    """Return a straight-line fit in the Fit schema (lists inside numpyarray nodes)."""
    return {"type": "Fit", "dict": {
        "variable_parameters": ["A_1", "A_0"],
        "opt_vector": {"type": "numpyarray", "list": [slope, intercept]},
        "cov_matrix": {"type": "numpyarray", "list": [{"type": "numpyarray", "list": row} for row in cov]},
    }}


def _new_line(slope: float, intercept: float, cov: list[list[float]], r2: float) -> dict:
    """Return a straight-line fit in the FittingBase schema (hex covariance, plain result list)."""
    flat = [v for row in cov for v in row]
    return {"type": "FittingBase", "name": "PolynomialScipy", "dict": {
        "variables": {"type": "FittingVariables", "dict": {"list": ["A_1", "A_0"]}},
        "optimised_results": [slope, intercept],
        "covariance_matrix": {**_numpyarray_node(flat), "shape": [2, 2]},
        "r2": r2,
    }}


def _fine_payload(mtype: str, fits: list[dict], old: float, new: float) -> dict:
    old_key, new_key = {"FineFrequency": ("old_frequencies", "new_frequency"), "XPi2Drag": ("old_drags", "new_drag")}[mtype]
    return {"type": "Result", "name": mtype, "dict": {
        "id": 7, "config": {"type": "Config", "dict": {old_key: {"Q1": old}}},
        "data": {"Q1": {"type": "Data", "dict": {"fits": fits, new_key: new}}},
    }}


def _newer_line(slope: float, intercept: float, cov: list[list[float]], r2: float) -> dict:
    """Return a straight-line fit in the 2026-05 FittingBase schema."""
    fit = _new_line(slope, intercept, cov, r2)
    del fit["dict"]["variables"]
    params = {"A_1": {"estimate": slope}, "A_0": {"estimate": intercept}}
    fit["dict"]["_variables"] = {"type": "FittingVariables", "dict": {"parameters": params, "constants": {}}}
    return fit


def test_fit_vector_reads_all_schemas():
    cov = [[0.04, 0.0], [0.0, 0.01]]
    old = decode.fit_vector(_old_line(2.0, 1.0, cov))
    new = decode.fit_vector(_new_line(2.0, 1.0, cov, 0.9))
    newer = decode.fit_vector(_newer_line(2.0, 1.0, cov, 0.9))
    for fv in (old, new, newer):
        assert fv.names == ("A_1", "A_0")
        assert fv.value("A_1") == 2.0 and fv.sd("A_1") == pytest.approx(0.2)
    assert old.r2 is None and new.r2 == 0.9
    assert decode.fit_vector({"type": "FittingBase", "dict": {"r2": 0.5}}).sd("f") is None


def test_crossing_matches_numerical_propagation():
    c0 = [[1e-4, -2e-5], [-2e-5, 4e-4]]
    c1 = [[2e-4, 1e-5], [1e-5, 1e-4]]
    first, second = decode.fit_vector(_old_line(1.0, 0.2, c0)), decode.fit_vector(_new_line(-1.5, 0.7, c1, 0.95))
    x, sd = decode.crossing(first, second)
    assert x == pytest.approx((0.7 - 0.2) / (1.0 + 1.5))
    rng = np.random.default_rng(0)
    p0 = rng.multivariate_normal([1.0, 0.2], c0, 200_000)
    p1 = rng.multivariate_normal([-1.5, 0.7], c1, 200_000)
    xs = (p1[:, 1] - p0[:, 1]) / (p0[:, 0] - p1[:, 0])
    assert sd == pytest.approx(xs.std(), rel=0.02)


def test_crossing_sd_is_none_without_covariance():
    first = decode.fit_vector(_old_line(1.0, 0.0, [[1e-4, 0.0], [0.0, 1e-4]]))
    second = decode.FitVector(("A_1", "A_0"), np.array([-1.0, 1.0]), None, None)
    assert decode.crossing(first, second) == (0.5, None)


def test_crossing_extractor_adds_error_and_lowest_r2():
    fits = [_new_line(1.0, 0.0, [[1e-4, 0.0], [0.0, 1e-4]], 0.97),
            _new_line(-1.0, 1.0, [[1e-4, 0.0], [0.0, 1e-4]], 0.91)]
    (row,) = extract(_fine_payload("FineFrequency", fits, old=5.0e9, new=5.0e9 + 0.5))
    _, sd = decode.crossing(*[decode.fit_vector(f) for f in fits])
    assert (row.param, row.old, row.new) == ("frequency", 5.0e9, 5.0e9 + 0.5)
    assert row.error == pytest.approx(sd) and row.r2 == 0.91
    (drag,) = extract(_fine_payload("XPi2Drag", fits[:1], old=1e-9, new=2e-9))
    assert drag.param == "drag" and drag.error is None  # one fit: no crossing


def test_ramsey_extractor_error_is_sd_of_f():
    obj = _ramsey_payload()
    q1 = obj["dict"]["data"]["Q1"]["dict"]
    q1["fit"]["dict"] |= {"variables": {"dict": {"list": ["A", "f"]}}, "optimised_results": [1.0, 5e6],
                          "covariance_matrix": {**_numpyarray_node([0.01, 0.0, 0.0, 2.5e7]), "shape": [2, 2]}}
    rows = {r.target: r for r in extract(obj)}
    assert rows["Q1"].error == pytest.approx(5000.0) and rows["Q1"].new == 4.9001e9
    assert rows["Q4"].error is None  # fit without a covariance
