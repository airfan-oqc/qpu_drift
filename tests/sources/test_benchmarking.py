import json
from datetime import date

import pandas as pd
import pytest

from qpu_drift.sources.benchmarking.clean import (
    add_gating_regime,
    assemble,
    classify,
    enrich_kpi,
    flatten_payload,
    gate,
    gate_good_values,
    gating_regime,
    r2_pregated,
    validate_l1,
    validate_l2,
)
from qpu_drift.sources.benchmarking.fetch import (
    Settings,
    _cache_range,
    _created_key,
    daterange,
    load_cached_calibrations,
    load_manifest,
)
from qpu_drift.sources.benchmarking.output_info import output_info_rows


def _no_dotenv(monkeypatch):
    """Stop from_env loading the repo's real .env."""
    monkeypatch.setattr(
        "qpu_drift.sources.benchmarking.fetch.load_dotenv", lambda *a, **k: False
    )


def test_daterange_is_inclusive():
    days = list(daterange(date(2025, 1, 1), date(2025, 1, 3)))
    assert days == [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    assert list(daterange(date(2025, 1, 1), date(2025, 1, 1))) == [date(2025, 1, 1)]


def test_created_key_is_filesystem_safe():
    assert _created_key("2026-08-13 22:01:47.149851") == "2026-08-13T220147_149851"


def test_settings_from_env_defaults(monkeypatch):
    _no_dotenv(monkeypatch)
    monkeypatch.delenv("OQC_URL", raising=False)
    monkeypatch.delenv("OQC_QPU_ID", raising=False)
    monkeypatch.setenv("OQC_AUTH_TOKEN", "tok")
    s = Settings.from_env(require_token=True)
    assert s.auth_token == "tok"
    assert s.url == "https://cloud.int.oqc.app/"
    assert s.qpu_id == "qpu:jp:3:673b1ad43c"


def test_settings_require_token_raises(monkeypatch):
    _no_dotenv(monkeypatch)
    monkeypatch.delenv("OQC_AUTH_TOKEN", raising=False)
    try:
        Settings.from_env(require_token=True)
    except RuntimeError as exc:
        assert "OQC_AUTH_TOKEN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when token is missing")


def _payload(created_at: str) -> dict:
    return {"general": {"created_at": created_at}, "one_qubit": {}, "two_qubit": {}}


def test_cache_range_dedups_forward_filled_payloads(tmp_path):
    # the API forward-fills: three query dates, two distinct created_at
    served = {
        date(2025, 1, 1): _payload("2025-01-01 08:00:00.0"),
        date(2025, 1, 2): _payload("2025-01-01 08:00:00.0"),  # forward-filled duplicate
        date(2025, 1, 3): _payload("2025-01-03 09:00:00.0"),
    }
    manifest = _cache_range(served.__getitem__, date(2025, 1, 1), date(2025, 1, 3),
                            tmp_path, log_every=0)

    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert files == ["2025-01-01T080000_0.json", "2025-01-03T090000_0.json"]
    assert len(manifest) == 3
    assert (manifest["status"] == "ok").all()
    assert manifest["created_at"].nunique() == 2
    payloads = load_cached_calibrations(tmp_path)
    assert len(payloads) == 2


def test_cache_range_records_empty_and_malformed(tmp_path):
    served = {
        date(2025, 1, 1): {},                       # empty -> no cache
        date(2025, 1, 2): {"one_qubit": {"Q1": {}}},  # truthy, no created_at
        date(2025, 1, 3): _payload("2025-01-03 09:00:00.0"),
    }
    manifest = _cache_range(served.__getitem__, date(2025, 1, 1), date(2025, 1, 3),
                            tmp_path, log_every=0)

    by_date = manifest.set_index("query_date")["status"]
    assert by_date[pd.Timestamp("2025-01-01")] == "empty"
    assert by_date[pd.Timestamp("2025-01-02")] == "malformed"
    assert by_date[pd.Timestamp("2025-01-03")] == "ok"
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["2025-01-03T090000_0.json"]


def test_cache_range_is_incremental_and_idempotent(tmp_path):
    first = {date(2025, 1, 1): _payload("2025-01-01 08:00:00.0")}
    _cache_range(first.__getitem__, date(2025, 1, 1), date(2025, 1, 1),
                 tmp_path, log_every=0)

    # a second, overlapping run extends the manifest without duplicating rows/files
    second = {
        date(2025, 1, 1): _payload("2025-01-01 08:00:00.0"),
        date(2025, 1, 2): _payload("2025-01-02 08:00:00.0"),
    }
    manifest = _cache_range(second.__getitem__, date(2025, 1, 1), date(2025, 1, 2),
                            tmp_path, log_every=0)

    assert len(manifest) == 2
    assert manifest["query_date"].is_monotonic_increasing
    assert len(sorted(tmp_path.glob("*.json"))) == 2

    reloaded = load_manifest(tmp_path)
    assert reloaded is not None
    assert len(reloaded) == 2


def test_cache_range_does_not_rewrite_existing_payload(tmp_path):
    key = "2025-01-01T080000_0.json"
    served = {date(2025, 1, 1): _payload("2025-01-01 08:00:00.0")}
    _cache_range(served.__getitem__, date(2025, 1, 1), date(2025, 1, 1),
                 tmp_path, log_every=0)

    # tamper with the cached file; a rerun must not overwrite an existing key
    (tmp_path / key).write_text(json.dumps({"sentinel": True}))
    _cache_range(served.__getitem__, date(2025, 1, 1), date(2025, 1, 1),
                 tmp_path, log_every=0)
    assert json.loads((tmp_path / key).read_text()) == {"sentinel": True}


def test_load_manifest_absent_returns_none(tmp_path):
    assert load_manifest(tmp_path) is None


def test_gate_decides_only_served_cells():
    # a decidable served cell gets a verdict; an undecidable null gets None
    assert gate("T1", 9.7e-5, 0.98) == "ok"
    assert gate("T1", 9.7e-5, None) == "no_r2"    # no r2 and nobody checked it
    assert gate("T1", 9.7e-5, None, pregated=True) == "ok"  # producer R2-checked it
    assert gate("T1", 300e-6, None) == "out_of_range"  # range needs no R2
    assert gate("T1", None, 0.95) == "out_of_range"  # null value, real fit (>=0.9)
    assert gate("T1", None, 0.5) == "low_r2"      # null value, poor fit (<0.9)
    assert gate("T1", None, None) is None         # undecidable, left to L2
    assert gate("T1", float("nan"), 0.95) == "out_of_range"  # NaN value == null


def test_gate_regates_served_values_intrinsically():
    # presence alone isn't enough; the gate re-applies the physics
    assert gate("T2", 5.0e-5, 0.80) == "low_r2"   # present but r2 < 0.9
    assert gate("T1", 300e-6, 0.99) == "out_of_range"  # present but out of range
    assert gate("T1", 300e-6, 0.80) == "low_r2"   # both fail -> R2 gate wins (producer order)


def test_gate_fro_is_range_gated_never_r2_gated():
    # fRO has no r2, so it's range-gated only; a null or exact 0 is undecidable
    assert gate("fRO", 0.96, None) == "ok"
    assert gate("fRO", 1.5, None) == "out_of_range"   # above the [0, 1] range
    assert gate("fRO", 0.0, None) is None             # exact 0 is the no-value sentinel
    assert gate("fRO", None, None) is None


def test_gate_served_zero_is_a_placeholder():
    # era 0 served fCX = 0 with no r2: the pre-filled 0.0 for a coupling whose RB2q never ran
    assert gate("fCX", 0.0, None) == "no_result"
    assert gate("fCX", 0.0, None, pregated=True) == "no_result"
    assert gate("T1", 0.0, None) == "no_result"
    assert gate("fCX", 0.9, None, pregated=True) == "ok"  # a real value still passes
    # fRO has no fit, so a 0 there is just absent
    assert gate("fRO", 0.0, None) is None
    # with an r2 the 0 is the failed-fit record (value 0, r2 0)
    assert gate("fCX", 0.0, 0.0) == "no_fit"
    assert classify("fCX", 0.0, None) == "no_result"


def test_gate_zero_r2_is_no_fit():
    # r2 == 0 means no fit for any metric, though only fCX emits it in practice
    assert gate("fCX", 0.0, 0.0) == "no_fit"      # raw 0/0 sentinel
    assert gate("fCX", None, 0.0) == "no_fit"     # value nulled, r2 kept at 0
    assert gate("fRB", None, 0.0) == "no_fit"     # sentinel is not fCX-only
    assert gate("fCX", 0.79, 0.5) == "low_r2"     # a genuine low-r2 fit is unaffected


def test_classify_delegates_to_gate_then_settles_the_null_tail():
    # a decidable cell agrees with gate exactly
    assert classify("T1", 9.7e-5, 0.98) == "ok"
    assert classify("T1", None, 0.5) == "low_r2"
    assert classify("T1", 9.7e-5, None, in_output_info=True) == "no_r2"
    assert classify("T1", 9.7e-5, None, pregated=True) == "ok"
    # undecidable nulls: unresolved off-cache, else warning + result_id split no_fit/no_result
    assert classify("T1", None, None) == "unresolved"          # run absent from cache
    assert classify("T1", None, None, in_output_info=True) == "no_result"
    # no_fit needs both a warning and a per-element result_id
    assert classify("fCX", None, None, warned=True, in_output_info=True,
                    has_result_id=True) == "no_fit"
    # missing either signal gives no_result
    assert classify("fCX", None, None, warned=True, in_output_info=True) == "no_result"
    assert classify("fCX", None, None, has_result_id=True,
                    in_output_info=True) == "no_result"
    # keyed on the signals, not the metric name
    assert classify("T1", None, None, warned=True, in_output_info=True) == "no_result"
    assert classify("T2", None, None, warned=True, in_output_info=True) == "no_result"
    assert classify("fRB", None, None, warned=True, in_output_info=True) == "no_result"
    assert classify("fRB", None, None, warned=True, in_output_info=True,
                    has_result_id=True) == "no_fit"
    # a served-null fRO recovered from output_info settles ok
    assert classify("fRO", 0.96, None, in_output_info=True) == "ok"
    assert classify("fRO", None, None, in_output_info=True) == "no_result"
    # fRO has no fit, so it's never no_fit
    assert classify("fRO", None, None) == "unresolved"
    assert classify("fRO", None, None, warned=True, in_output_info=True,
                    has_result_id=True) == "no_result"


def _kpi_payload(created_at="2026-08-13 22:01:47.149851", *, with_r2=True, openpulse=False):
    """Build a minimal real-shaped payload with 2 qubits and 1 coupling."""
    q1 = {"qubit": 1, "T1": 9.7e-5, "T2": 4.0e-5, "fRB": 0.998, "fRO": 0.96}
    q2 = {"qubit": 2, "T1": None, "T2": None, "fRB": None, "fRO": 0.95}
    if with_r2:
        q1 |= {"T1_r2": 0.98, "T2_r2": 0.99, "fRB_r2": 0.99}
        q2 |= {"T1_r2": 0.5, "T2_r2": None, "fRB_r2": None}  # low_r2, unresolved, unresolved
    cpl = {"coupling": {"control_qubit": 1, "target_qubit": 10}, "fCX": None}
    if with_r2:
        cpl |= {"fCX_r2": None}  # padded null, unresolved
    payload = {
        "general": {"created_at": created_at, "topology": [[1, 10]],
                    "version": 1, "max_shots": 10000},
        "one_qubit": {"1": q1, "2": q2},
        "two_qubit": {"1-10": cpl},
    }
    if openpulse:
        payload["openpulse_features"] = {"open_pulse": {"frames": {}}}
    return payload


def test_flatten_payload_shape_is_ungated_spine():
    run, rows = flatten_payload(_kpi_payload(openpulse=True))
    assert run == {"created_at": "2026-08-13 22:01:47.149851",
                   "topology": [[1, 10]], "has_openpulse": True}
    # 2 qubits x 4 metrics + 1 coupling x fCX
    assert len(rows) == 9
    # ungated: served values as-is, no status or provenance columns
    fro = next(r for r in rows if r["element"] == "1" and r["metric"] == "fRO")
    assert fro["value"] == 0.96 and fro["error"] is None and fro["r2"] is None
    assert set(fro) == {"created_at", "element", "element_type", "metric",
                        "value", "error", "r2"}
    types = {(r["element"], r["metric"]): r["element_type"] for r in rows}
    assert types[("1", "T1")] == "qubit"
    assert types[("1-10", "fCX")] == "coupling"


def test_flatten_carries_served_value_and_r2_verbatim():
    _, rows = flatten_payload(_kpi_payload())
    by = {(r["element"], r["metric"]): r for r in rows}
    assert by[("1", "T1")]["value"] == 9.7e-5 and by[("1", "T1")]["r2"] == 0.98
    assert by[("2", "T1")]["value"] is None and by[("2", "T1")]["r2"] == 0.5
    # a null coupling with a padded null r2 comes through untouched
    assert by[("1-10", "fCX")]["value"] is None and by[("1-10", "fCX")]["r2"] is None


def test_payload_without_r2_columns_yields_none_r2():
    _, rows = flatten_payload(_kpi_payload(with_r2=False))
    by = {(r["element"], r["metric"]): r for r in rows}
    assert by[("2", "T1")]["value"] is None and by[("2", "T1")]["r2"] is None
    assert by[("1", "T1")]["value"] == 9.7e-5 and by[("1", "T1")]["r2"] is None


def test_assemble_skips_malformed_and_dedups():
    good = _kpi_payload("2026-08-13 22:01:47.149851")
    dup = _kpi_payload("2026-08-13 22:01:47.149851")            # same created_at
    empty = {}                                                  # empty date
    malformed = {"general": {}, "one_qubit": {}, "two_qubit": {}}  # no created_at
    runs, kpi = assemble([good, dup, empty, malformed])
    assert len(runs) == 1
    assert runs["created_at"].dt.tz is None                     # left naive
    # ungated spine: no gating_regime on runs, no status or provenance on kpi
    assert list(runs.columns) == ["created_at", "topology", "has_openpulse"]
    assert list(kpi.columns) == ["created_at", "element", "element_type",
                                 "metric", "value", "error", "r2"]
    # rejects are still there: Q2 T1 keeps its null value and r2
    q2t1 = kpi[(kpi["element"] == "2") & (kpi["metric"] == "T1")].iloc[0]
    assert pd.isna(q2t1["value"]) and q2t1["r2"] == 0.5


def test_gate_good_values_nulls_rejects_keeps_r2():
    _, kpi = assemble([_kpi_payload("2026-08-13 22:01:47.149851")])
    gated = gate_good_values(kpi)
    assert list(gated.columns) == list(kpi.columns)             # no new columns
    by = {(r.element, r.metric): r for r in gated.itertuples()}
    # a good value survives with its r2
    assert by[("1", "T1")].value == 9.7e-5 and by[("1", "T1")].r2 == 0.98
    # a low-r2 reject is nulled but keeps its r2
    assert pd.isna(by[("2", "T1")].value) and by[("2", "T1")].r2 == 0.5
    # fRO has no r2, so it's gated on range alone
    assert by[("1", "fRO")].value == 0.96
    # only ok cells keep a value
    n_good = int(gated["value"].notna().sum())
    assert n_good == sum(gate(r.metric, r.value, r.r2) == "ok"
                         for r in kpi.itertuples())


def test_r2_pregated_only_era1_outside_the_t1_t2_crash():
    assert not r2_pregated("2024-08-17 02:50:01", "fRB")   # era 0: no producer gate
    assert r2_pregated("2024-10-15 23:00:00", "fRB")       # era 1: filter() R2-checked
    assert r2_pregated("2025-04-10 23:02:09", "T1")        # last run before the crash
    assert not r2_pregated("2025-04-11 23:02:55", "T1")    # maac 1.5.3 crash window
    assert not r2_pregated("2025-07-06 23:13:07", "T2")
    assert r2_pregated("2025-05-18 20:14:10", "fRB")       # crash hit T1/T2 only
    assert r2_pregated("2025-05-18 20:14:10", "fCX")
    assert not r2_pregated("2025-07-07 23:10:10", "fRB")   # eras 2-3 serve R2 themselves
    assert not r2_pregated("2026-08-13 22:01:47", "fCX")


def test_gate_good_values_keeps_no_r2_values_only_where_pregated():
    def kept(ts):
        _, kpi = assemble([_kpi_payload(ts, with_r2=False)])
        gated = gate_good_values(kpi)
        return {(r.element, r.metric) for r in gated.itertuples() if pd.notna(r.value)}

    fro = {("1", "fRO"), ("2", "fRO")}                     # fRO never needs an R2
    fits = {("1", "T1"), ("1", "T2"), ("1", "fRB")}
    assert kept("2024-06-01 23:00:00") == fro              # era 0: fits dropped
    assert kept("2025-01-01 23:00:00") == fro | fits       # era 1: producer pregated
    assert kept("2025-05-01 23:00:00") == fro | {("1", "fRB")}  # crash window: T1/T2 dropped


def test_enrich_kpi_no_r2_on_a_matched_run_pregated_off_cache():
    # era-1 T1 with no R2: ok off-cache (pregated), no_r2 on a matched run
    ts = "2025-01-01 23:00:00"
    runs, kpi = assemble([_kpi_payload(ts, with_r2=False)])
    runs = add_gating_regime(runs)
    l1_good = gate_good_values(kpi)

    def status(measured_at):
        prov = pd.DataFrame([{"metric": "fRB", "element": "1", "value": float("nan"),
                              "r2": 0.99, "warned": False, "result_id": pd.NA,
                              "measured_at": pd.Timestamp(measured_at)}])
        out = enrich_kpi(kpi, prov)
        by = {(r.element, r.metric): r.value_status for r in out.itertuples()}
        return by, validate_l2(runs, out, l1_good=l1_good)

    off, _ = status("2024-12-30 18:00:00")                # no run match
    assert off[("1", "T1")] == "ok" and off[("1", "fRB")] == "ok"
    on, checks = status("2025-01-01 20:00:00")            # matched run
    assert on[("1", "T1")] == "no_r2" and on[("1", "fRB")] == "ok"
    assert checks["l1_good_not_l2_ok"] == 2               # T1 and T2 of Q1
    assert checks["ok_no_r2_not_pregated"] == 0


def test_add_gating_regime_stamps_era():
    runs, _ = assemble([_kpi_payload("2026-08-13 22:01:47.149851")])
    enriched = add_gating_regime(runs)
    assert list(enriched.columns) == ["created_at", "topology", "has_openpulse",
                                      "gating_regime"]
    assert enriched.iloc[0]["gating_regime"] == "era3_postproc"  # 2026-08-13 >= gate


def _output_info(measured_at):
    """Build provenance rows from a real-shaped output_info file."""
    oi = {
        "result_ids": {
            "t1": {"0": 2991},                       # group-keyed
            "t2e": {},
            "rb1q": {"Q1": 3003, "Q2": 3004},        # per qubit
            "zmap": {"0": 3035},
            "rb2q": {"C1-T10": 3041},                # per coupling ('C1-T10' -> '1-10')
        },
        "fit_quality": {
            "t1s": {"Q1": 0.98},                     # only Q1 has an R2
            "t2s": {"Q1": 0.99},                     # Q2 absent -> reads as no_result
            "fidelities_1q": {"Q1": 0.99, "Q2": 0.99},  # Q2 good fit QCaaS didn't serve
            "fidelities_ro": {},                     # fRO never carries R2
            "fidelities_2q": {},                     # C1-T10 absent -> falls to warning
        },
        "benchmarks": {
            "fidelities_1q": {"Q2": 0.997},          # Q2 pre-gate value, in range -> ok
        },
        "warnings": ["Did not return a 2Q gate fidelity for C1-T10"],
    }
    return output_info_rows(oi, measured_at)


def test_output_info_rows_normalise_keys_and_carry_r2_value_warned():
    rows = _output_info(pd.Timestamp("2026-08-13 18:10:05"))
    by = {(r["metric"], r["element"]): r for r in rows}
    # coupling keys normalise to '1-10'; the no-fit warning is carried, R2 absent
    assert by[("fCX", "1-10")]["warned"] is True
    assert pd.isna(by[("fCX", "1-10")]["r2"])
    assert pd.notna(by[("fCX", "1-10")]["result_id"])   # rb2q recovery id survives
    assert by[("fRB", "1")]["result_id"] == 3003         # per-qubit id
    # Q2 fRB: fit_quality R2 and benchmarks pre-gate value both surface
    assert by[("fRB", "2")]["r2"] == 0.99 and by[("fRB", "2")]["value"] == 0.997
    # group-keyed coherence metric: no per-element id, R2 comes from fit_quality
    assert pd.isna(by[("T1", "1")]["result_id"]) and by[("T1", "1")]["r2"] == 0.98
    # Q2 T2 leaves no trace, so no row (read later as no_result)
    assert ("T2", "2") not in by


def test_output_info_rows_r2_without_result_id():
    # fit quality but no rb1q ids (5 of 663 real files): R2 surfaces, id stays NA
    oi = {
        "result_ids": {"rb2q": {"C1-T10": 42}},          # no rb1q group at all
        "fit_quality": {"fidelities_1q": {"Q3": 0.997}},   # but an R2 is present
    }
    rows = output_info_rows(oi, pd.Timestamp("2026-08-13 18:10:05"))
    frb3 = next(r for r in rows if r["metric"] == "fRB" and r["element"] == "3")
    assert frb3["r2"] == 0.997 and pd.isna(frb3["result_id"])


def test_enrich_kpi_widens_spine_and_resolves_nulls():
    ts = "2026-08-13 22:01:47.149851"
    _, kpi = assemble([_kpi_payload(ts)])          # ungated spine
    prov = pd.DataFrame(_output_info(pd.Timestamp("2026-08-13 18:10:05")))
    out = enrich_kpi(kpi, prov)

    # four L2 columns are added
    assert list(out.columns) == list(kpi.columns) + [
        "value_status", "value_source", "result_id", "measured_at"]
    status = {(r.element, r.metric): r.value_status for r in out.itertuples()}
    # fCX unresolved + a no-fit warning, no fit_quality R2 -> no_fit
    assert status[("1-10", "fCX")] == "no_fit"
    # Q2 fRB: good output_info R2 + in-range value -> ok
    assert status[("2", "fRB")] == "ok"
    # Q2 T2 unresolved + absent from fit_quality, no warning -> no_result
    assert status[("2", "T2")] == "no_result"
    # a served cell keeps its verdict
    assert status[("1", "T1")] == "ok"
    # Q2 fRB's null falls back to the output_info value
    frb2 = out[(out["element"] == "2") & (out["metric"] == "fRB")].iloc[0]
    assert frb2["value"] == 0.997 and frb2["value_source"] == "output_info"
    assert frb2["result_id"] == 3004
    # served value -> qcaas; unrecovered no_result -> none
    assert out[(out["element"] == "1") & (out["metric"] == "T1")
               ].iloc[0]["value_source"] == "qcaas"
    assert out[(out["element"] == "2") & (out["metric"] == "T2")
               ].iloc[0]["value_source"] == "none"
    # an era-3 low_r2 reject on a matched run is marked ds1, value still NaN
    t1_2 = out[(out["element"] == "2") & (out["metric"] == "T1")].iloc[0]
    assert t1_2["value_status"] == "low_r2" and t1_2["value_source"] == "ds1"
    assert pd.isna(t1_2["value"])


def test_enrich_kpi_resolved_r2_and_value_split_low_r2_ok_out_of_range():
    # a null served cell settled from output_info: R2 gates first, then the range
    ts = "2026-08-13 22:01:47.149851"
    _, kpi = assemble([_kpi_payload(ts)])
    at = pd.Timestamp("2026-08-13 18:10:05")
    common = {"warned": False, "result_id": pd.NA, "measured_at": at}

    def resolve(value, r2):
        prov = pd.DataFrame([{"metric": "fRB", "element": "2",
                              "value": value, "r2": r2, **common}])
        out = enrich_kpi(kpi, prov)
        row = out[(out["element"] == "2") & (out["metric"] == "fRB")].iloc[0]
        return row["value_status"]

    assert resolve(0.5, 0.80) == "low_r2"        # R2 gate wins before the range check
    assert resolve(0.998, 0.95) == "ok"          # good R2, in-range value
    assert resolve(1.5, 0.95) == "out_of_range"  # good R2, value out of [0, 1]


def test_enrich_kpi_guards_runs_absent_from_cache():
    _, kpi = assemble([_kpi_payload("2026-08-13 22:01:47.149851")])
    # a different day -> no run match, so cells stay unresolved
    prov = pd.DataFrame(_output_info(pd.Timestamp("2026-08-12 18:10:05")))
    out = enrich_kpi(kpi, prov)
    assert (out["value_status"] == "unresolved").any()
    assert not (out["value_status"] == "no_result").any()


def test_enrich_kpi_empty_output_info_file_settles_no_result():
    # an all-empty file (2026-08-10 13:53:57) still matches its run, so nulls are no_result
    _, kpi = assemble([_kpi_payload("2026-08-13 22:01:47.149851")])
    prov = pd.DataFrame([{"metric": pd.NA, "element": pd.NA, "value": pd.NA, "r2": pd.NA,
                          "warned": pd.NA, "result_id": pd.NA,
                          "measured_at": pd.Timestamp("2026-08-13 18:10:05")}])
    out = enrich_kpi(kpi, prov)
    assert not (out["value_status"] == "unresolved").any()
    assert (out["value_status"] == "no_result").any()
    assert out["result_id"].isna().all()


def test_enrich_kpi_requires_a_nonempty_cache():
    # L2 is enrichment, so no cache is an error
    _, kpi = assemble([_kpi_payload()])
    with pytest.raises(ValueError, match="non-empty output_info"):
        enrich_kpi(kpi, pd.DataFrame())


def test_validate_l1_flags_and_counts():
    runs, kpi = assemble([_kpi_payload()])
    kpi = gate_good_values(kpi)
    checks = validate_l1(runs, kpi)
    assert checks["present_out_of_range"] == 0
    assert checks["present_low_r2"] == 0
    assert checks["present_zero_r2"] == 0
    assert checks["present_no_r2_not_pregated"] == 0
    assert checks["duplicate_created_at"] == 0
    assert checks["nat_created_at"] == 0
    # n_good counts the values that survived the gate
    assert checks["n_good"] == int(kpi["value"].notna().sum())
    assert checks["n_good"] > 0


def test_validate_l2_flags_and_counts():
    runs, kpi = assemble([_kpi_payload()])
    runs = add_gating_regime(runs)
    # cache for another day: nulls stay unresolved but the L2 checks still run
    prov = pd.DataFrame(_output_info(pd.Timestamp("2026-08-12 18:10:05")))
    kpi = enrich_kpi(kpi, prov)
    checks = validate_l2(runs, kpi)
    assert checks["ok_out_of_range"] == 0
    assert checks["ok_low_r2"] == 0
    assert checks["ok_no_r2_not_pregated"] == 0
    assert checks["out_of_range_low_r2"] == 0
    assert checks["low_r2_zero_sentinel"] == 0
    assert checks["unknown_status"] == 0
    assert checks["unknown_value_source"] == 0
    assert checks["ok_null_value"] == 0
    assert checks["ok_ds1"] == 0
    assert checks["free_source_null"] == 0
    assert checks["duplicate_created_at"] == 0
    assert checks["n_unresolved"] > 0                     # QCaaS-only build has padded nulls


def test_validate_l2_cross_level_l1_good_equals_l2_ok():
    # with no output_info recovery, L1 good and L2 ok coincide
    runs, kpi = assemble([_kpi_payload()])
    runs = add_gating_regime(runs)
    l1_good = gate_good_values(kpi)
    prov = pd.DataFrame(_output_info(pd.Timestamp("2026-08-12 18:10:05")))  # non-matching
    l2 = enrich_kpi(kpi, prov)
    checks = validate_l2(runs, l2, l1_good=l1_good)
    assert checks["l1_good_not_l2_ok"] == 0        # no served-good cell demoted (hard)
    assert checks["l2_ok_recovered"] == 0          # nothing recovered on a non-matching cache


def test_validate_l2_cross_level_recovered_ok_is_not_a_demotion():
    # a matching cache promotes Q2 fRB to ok; that's recovery, not a demotion
    runs, kpi = assemble([_kpi_payload("2026-08-13 22:01:47.149851")])
    runs = add_gating_regime(runs)
    l1_good = gate_good_values(kpi)
    prov = pd.DataFrame(_output_info(pd.Timestamp("2026-08-13 18:10:05")))
    l2 = enrich_kpi(kpi, prov)
    checks = validate_l2(runs, l2, l1_good=l1_good)
    assert checks["l2_ok_recovered"] >= 1          # served-null fRB recovered to ok
    assert checks["l1_good_not_l2_ok"] == 0        # no served-good value demoted


def test_gating_regime_boundaries():
    # half-open: each era starts at its boundary timestamp
    assert gating_regime("2024-08-17 02:50:01") == "era0_ungated"
    assert gating_regime("2024-09-09 23:59:59") == "era0_ungated"
    assert gating_regime("2024-09-10 00:00:00") == "era1_filter"
    assert gating_regime("2025-07-06 23:59:59") == "era1_filter"
    assert gating_regime("2025-07-07 00:00:00") == "era2_ungated"
    assert gating_regime("2025-08-21 23:59:59") == "era2_ungated"
    assert gating_regime("2025-08-22 00:00:00") == "era3_postproc"
    assert gating_regime(pd.Timestamp("2026-09-14 22:01:36")) == "era3_postproc"
