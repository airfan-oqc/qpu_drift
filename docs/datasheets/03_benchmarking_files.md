# Datasheet 3: QCaaS Benchmarking Files (`benchmarking_json`)

This dataset holds the device's **KPIs**, with one small JSON file per benchmarking run. Each file has the measured performance of every qubit and coupling: the coherence times (`T1`, `T2`) and the gate and readout fidelities (`fRB`, `fCX`, `fRO`). From 2025-07-07 the fitted metrics also have an uncertainty and a fit quality (R²). `fRO` isn't fitted, so it only has a value. The records come from the upload step of the benchmarking routine (`run_benchmarking` in `oqc-calibration-routines`). They're made to report device performance, not for analysis, so treat the data as found data. This sheet describes what's in the files and any open questions in section 9.


## 1. Overview

| Field | Value |
|---|---|
| QPU ID | `qpu:jp:3:673b1ad43c` |
| Source name | QCaaS benchmarking run record (`benchmark_json`) |
| Source of truth | QCaaS DB, read via QCaaS client. The producer also writes its own `output_info/…_benchmarking.json` on the server - this has a different shape, and from 2025-08-22 it's saved after the gate (section 3). |
| Retrieval | `OQCClient.get_calibration(date=D)` returns the latest record created strictly before `D` 00:00. The fetch script queries one day at a time and removes duplicates by `created_at` (section 8). |
| Format | Plain nested JSON |
| Unit | One file is one benchmarking run. |
| Records | One per benchmarking run that the day-by-day query can reach. A run followed by a later run on the same day isn't returned (section 3). |
| Time span (`created_at`) | From 2024-05-24 20:16, the earliest record the QCaaS client serves. The snapshot used for this sheet ends on 2026-09-14 22:01. |
| Cadence | Near-daily but irregular (sections 3 and 5). |
| Top-level keys | `general`, `one_qubit` and `two_qubit`, plus `openpulse_features` from 2026-05-21 |
| Device coverage | 32 qubits, the same set in every run (IDs 1-35 except 3, 8 and 29). 32 directed couplings, which are the calibrated ECR edges listed in `topology`. The `two_qubit` keys match `topology` in every run. The edge set changes during the archive (section 7). |
| Producer | `run_benchmarking()` in `oqc-calibration-routines` (`benchmarking.py`) builds the payload with `get_benchmark_json()` and uploads it to the QCaaS database. |
| Join key | `created_at`, which is unique per run (section 6). |

Counts in this sheet come from the snapshot used to write it. A newer snapshot will give different numbers, but the rules stay the same.

## 2. Motivation and intended use

This data exists to report **how well the calibrated device performs**. Each evening the routine measures the standard KPIs and uploads them to the QCaaS database. It was built for reporting, not analysis, so the values are already fitted. In later eras they're also already quality-filtered ("gated", section 3). The data is most useful as a daily time series of each qubit's and coupling's KPIs, for example to measure how much they drift and how noisy they are. To check a value, or to find one that was filtered out, use the run's `output_info` file. It links each value to its raw measurement record in datasheet 1 (section 6).

## 3. Provenance and collection

Each record comes from the upload step of `run_benchmarking(env)`. This is the last stage of the evening calibration window, which runs mixer calibration, then 1q, 2q and benchmarking (datasheets 1 and 2). The routine runs T1, T2echo, 1Q RB, ZMap readout and 2Q RB, and fits each curve. Readout is scored from shot counts instead. Then `get_benchmark_json()` formats the results per qubit and per coupling, and `run_benchmarking` uploads the result to the QCaaS database.

- **There are two linked stores.** The QCaaS database holds the served payload. It's the source of truth, and it's the only store with `*_error` and `openpulse_features`. The calibration server keeps the producer's own `output_info/…_benchmarking.json`, with `result_ids`, `benchmarks`, `fit_quality` (R²) and `warnings`. Section 6 uses it to recover R² and to trace rejected fits back to datasheet 1.
- **There are two schema shapes, split by date.** Records up to 2026-05-18 have only `general`, `one_qubit` and `two_qubit`. Records from 2026-05-21 also have `openpulse_features`. The number of frames in that block also changes: 263 up to 2026-08-31, and 264 from 2026-09-01, when `q7_freq_shift` was added. So don't hard-code the frame count.
- **`created_at` marks the end of the run.** The producer sets it with `datetime.now()` on the calibration server while it builds the payload. It's UTC with no timezone. In this snapshot about 95% of `created_at` times fall between 20:00 and 01:00, and 32 runs fall outside that window.
- **The query returns one record per day.** `get_calibration(date=D)` returns the latest record created before `D` 00:00. So a day-by-day query only sees the last run of each day, even though some days have re-runs (up to 3 `output_info` files on one day in this snapshot). It always returns whole records, never single fields, so a `null` is never filled in from an earlier run.
- **Values are gated, and the gate changed over time.** Before upload, the current producer sets to null any fit with R² < 0.9 and any value outside its physical range. It also fills every unmeasured element with `null`, so a `null` can mean either case. The gate went through four eras (below). [Section 6](#6-preprocessing-and-cleaning) explains how to tell the two kinds of null apart.
- **Errors and R² only exist from 2025-07-07.** From that date `T1`, `T2`, `fRB` and `fCX` have `*_error` and `*_r2`. Earlier records (eras 0 and 1) only have values. `fRO` never has either, because it's a direct measurement, not a fit (section 4).

<details>
<summary><b>Gating eras and store details</b></summary>

| Era (`gating_regime`) | Dates | Runs (this snapshot) | Producer gate |
|---|---|---|---|
| 0 `era0_ungated` | before 2024-09-10 (last run 2024-08-17) | 75 | none |
| 1 `era1_filter` | 2024-09-10 to 2025-07-06 | 223 | R² < 0.9 only |
| 2 `era2_ungated` | 2025-07-07 to 2025-08-21 | 40 | none |
| 3 `era3_postproc` | from 2025-08-22 | 317 | R² < 0.9 and range (`T1` 0-150 µs, `T2` 0-200 µs, fidelities including `fRO` 0-1) |

- **The 2025-11-08 exception.** This era-3 record is an ungated payload in the old format, with no `*_error` or `*_r2` keys. `T1`, `T2` and `fRB` have values only for Q1 and Q2, and two `fCX` values are just above 1. Its `output_info` file exists but its `fit_quality` is empty, so no store has an R² for it.
- **The 2026-08-10 empty record.** This era-3 record (13:56, outside the usual window) has the new keys, but every value, error and R² is null. The run ended about 2.5 minutes after it started, so nothing was measured. Its `output_info` file has all the usual blocks, but they're empty.
- **`output_info` is saved after the gate.** From 2025-08-22 `post_process_benchmark()` sets rejected `benchmarks` values to null before the file is written. So the file only has unfiltered values before that date. After it, a rejected value is gone, and only its `fit_quality` and `result_id` remain.
- **Coverage.** There's no benchmarking `output_info` file between 2024-05-19 and 2024-10-14 (the only earlier ones are from bring-up in April and May 2024). So era-0 runs have neither an R² nor a `result_id` (section 6).
- **Shape counts.** In this snapshot, 570 records have no `openpulse_features` and 85 have it. Of those 85, 77 have 263 frames and 8 have 264. 299 records have no `*_error` or `*_r2` keys: the 298 in eras 0 and 1, plus 2025-11-08.

</details>


## 4. Composition and structure

**Format and encoding:**
- Plain nested JSON with **three top-level keys**: `general`, `one_qubit` and `two_qubit`. Later records add a fourth, `openpulse_features`.
- `general` holds **run metadata**. Only `created_at` and `topology` change between runs. `version` (1), `max_shots` (10,000) and `native_gates` (`[ECR, I, RZ, SX, X]`) are the **same in every record**.
- `created_at` is an ISO-like **string** with no timezone (e.g. `2026-08-13 22:01:47.149851`).
- `one_qubit` has one entry per qubit (32). `two_qubit` has one entry per coupling (32), and these are the device's calibrated ECR edges listed in `topology`.
- **Units:** `T1` and `T2` are in **seconds**, `fRB`, `fRO` and `fCX` are **fractions** (0 to 1), and frame frequencies are in **Hz**.

**Shape:**

```text
{
  general:            { created_at, topology,  version, max_shots, native_gates }
  one_qubit:          { "<qubit>":      { qubit, T1, T2, fRB, fRO  (+ _error/_r2) } }   # x 32
  two_qubit:          { "<ctrl>-<tgt>": { coupling{ control_qubit, target_qubit }, fCX (+ _error/_r2) } }   # x 32
  openpulse_features: { open_pulse:     { frames, ports, waveforms, constraints, version } }   # later records only
}
```

**KPI fields**

Each KPI is a value `<metric>`. From 2025-07-07, every KPI except `fRO` also has a matching `<metric>_error` and `<metric>_r2`:

| Metric | Element | Source measurement | Example (value ± error, R²) |
|---|---|---|---|
| `T1` | qubit | T1 | 9.75e-05 ± 1.58e-05, R² 0.978 |
| `T2` | qubit | T2echo | 4.05e-05 ± 2.55e-06, R² 0.983 |
| `fRB` | qubit | 1Q RB | 0.99813 ± 1.74e-04, R² 0.992 |
| `fRO` | qubit | ZMap readout | 0.9606 (no error or R²) |
| `fCX` | coupling | 2Q (ECR) RB | 0.790 ± 0.027, R² 0.953 |

**`openpulse_features`**

This block is only on records from 2026-05-21. It's a copy of the live pulse setup (`frames`, `ports`, `waveforms` and `constraints`), built directly from the hardware model when the benchmark runs (`create_openpulse_dict(hardware_model)`). So its frequencies match the hardware-model archive (datasheet 2) exactly where the two overlap. Datasheet 2 is the source for frame history before 2026-05-21, and this block continues it after datasheet 2 ends. Only the per-frame `frequency` changes with calibration. `bandwidth_centre` is constant for each frame, and `phase` is always `0`.

<details>
<summary><b>openpulse_features sub-keys and frame fields</b></summary>

| Sub-key | Contents |
|---|---|
| `frames` | **263** named pulse channels (264 from 2026-09-01): 32 qubit `_drive`, 128 cross-resonance lines (64 `_cross_resonance` + 64 `_cross_resonance_cancellation`), 32 readout `_measure`, 32 `_acquire`, 32 `_second_state`, and 7-8 `_freq_shift`. Each holds that channel's frequency setpoint. The fields are explained below. |
| `ports` | **64** entries, e.g. `channel_1` with `{associated_qubits, direction, type}` |
| `waveforms` | **8** named waveform definitions |
| `constraints` | Fixed pulse and waveform limits (`awg_frequency_bandwidth`, `max_pulse_duration`, `min_pulse_duration`, `min_pulse_time`, `max_scale`, `max_waveform_amplitude`, `pulse_control_contraints`) |
| `version` | OpenPulse schema tag (the string `"0"`) |

Each frame describes one **pulse channel**, a logical channel on a physical port, with five fields:
- `frequency` (Hz): the channel's operating setpoint, which is the qubit drive, cross-resonance or readout frequency.
- `bandwidth_centre` (Hz): the centre of its mixer and LO band.
- `port_id`: the AWG or digitiser port it maps to (e.g. `channel_19`).
- `qubits`: the qubits it addresses. Cross-resonance frames and their cancellation frames have two, and every other frame has one.
- `phase`: always `0`. It's hard-coded and has no calibrated value.

Note several frames can share a port (e.g. `q10_drive` and `q10_q11_cross_resonance` both use `channel_19`).

</details>

## 5. Distribution and statistics

- **There's at most one record per day (section 3), with some idle stretches.** On a day with no new run, the query returns the previous record again (forward-fill). The largest gap between runs is about 60 days, during early bring-up (2024-08-17 to 2024-10-15). After that, the largest gaps are about 13 days (March 2025 and March 2026).
- **Metric coverage is partial and varies by metric.** The producer sets low-quality fits to null and fills unmeasured elements with null, so `null` is common. How common depends on the metric (this snapshot):

  | Metric | Cells populated | Median per run |
  |---|---|---|
  | `T1` | 81% | 30 / 32 qubits |
  | `T2` | 70% | 25 / 32 qubits |
  | `fRB` | 85% | 28 / 32 qubits |
  | `fRO` | 99% | 32 / 32 qubits |
  | `fCX` | 87% | 29 / 32 couplings |
- **Payloads are small.** A file without `openpulse_features` is about 5 to 16 KB. Files with `openpulse_features` are about 57 to 62 KB.
- **Two query dates have no usable payload.** **2024-05-24** returns nothing, because the first record (2024-05-24 20:16) was created after that query's midnight cutoff (section 3). **2026-02-26** returns the 2026-02-25 run without a `general.created_at`. That run was the device's first on a newer producer install. Its `get_benchmark_json` merged all blocks into one flat dict, and `7555377` fixed this the next day. The device then went back to its older install until 2026-05-21. That's why `openpulse_features` only starts then, even though the producer added it on 2026-02-03 (`a4109bd`; calibration-server runner logs). Both dates are left out of the archive, so the 2026-02-25 run is missing even though its `output_info` file exists.

<details><summary><b>Numbers for this snapshot</b></summary>

- **Span and cadence.** 655 records on 655 calendar days, across an 844-day span. So about 78% of days have a new record (about 0.78 runs a day), and on the other 189 days the query returns an earlier record. The fetch made 845 queries.
- **Size.** 11.49 MB across the 655 files. The median file is about 14.8 KB, and sizes range from 5.2 to 62 KB.

</details>

## 6. Preprocessing and cleaning

The build cleans the raw payloads into **two levels**, each with a run table and a long KPI table. Both levels are built directly from the raw payloads. Level 2 doesn't read Level 1's output, because Level 1 throws the rejected values away. Which one you want depends on your question and your access:

- **Level 1: good values only, no server needed.** The QCaaS payloads with one shared gate applied, so any value that's present is a good one. Drop the nulls and you have a table of good values (78,590 of 104,800 cells in this snapshot).
- **Level 2: full fit information, rejected and missing values, needs server access.** The same payloads combined with the server `output_info` and the DS1 records. It labels every cell, keeps the rejected values, and links each cell back to its raw measurement in datasheet 1. Use it to study rejects or to trace a fit back to DS1.

Both levels pass every cell through the same **shared gate**: a value is good if it's physically possible and, for fitted metrics, has R² ≥ 0.9 (or comes from an era whose producer already checked its R²). Level 1 uses the gate to filter and Level 2 uses it to label. The two can disagree in principle, because Level 1 takes the R² from QCaaS and decides `pregated` by date, while Level 2 also uses the `output_info` R². The build checks that they agree (`l1_good_not_l2_ok`, which must be 0), and in this snapshot Level 2's `ok` cells are exactly Level 1's good values. Level 2 adds information, not more good values. The build counts any cell it would change to `ok` as `l2_ok_recovered`, which should be 0.

### Level 1: QCaaS-only good values

- **Run table** (`created_at`, `topology`, `has_openpulse`). `created_at` is a unique key (no duplicates, and runs are hours apart), and every other datasheet joins on it. It has no timezone, so only add `tz="UTC"` when joining to a timezone-aware clock. `topology` can list the same edges in a different order, and the edge set itself changes twice in this snapshot (section 7), so sort it before comparing. The other `general` fields (`version`, `max_shots`, `native_gates`) never change (section 4), so they're not kept. There's no `gating_regime` either, since it follows from `created_at`.
- **KPI table** (one row per `created_at`, `element`, `metric`, plus `element_type`, `value`, `r2`, `error`). Values that fail the gate are set to null, so there's no `value_status` and no recovery columns. The raw numbers are still in the payloads. The table is long, so the same gate applies to every metric. Pivot it to wide when you analyse it. Coupling rows follow each run's own topology, so a reversed edge has no row at all rather than a `null`.

> [!NOTE]
> **Low but valid values.** The gate keeps anything physically possible, so values near the random-guess floor still pass. `fRO` has no quality measure at all, and in this snapshot its good values go down to about 0.5. A few era-0 cells sit exactly on the depolarising floor (`fCX` at 0.25, `fRB` at 0.5), though they're `no_r2` anyway. Filter these out in your analysis if needed (e.g. `fRO > 0.5`, `fCX > 0.25`, `fRB > 0.5`). `out_of_range` is only for values that are physically impossible.

### Level 2: provenance, combined values and recovery

Level 2 is built from the raw payloads plus `output_info` and DS1 (section 8). It never changes a Level 1 number. It reads the values from before the shared gate, so it keeps the rejects. It adds:

- **`value_status`**, one of seven states (`ok`, `out_of_range`, `low_r2`, `no_fit`, `no_r2`, `no_result`, `unresolved`): the result of the shared gate, plus how a padded null is resolved (both below). `build-benchmarking-l2` prints the count per state (section 8).
- **A combined `value` and its `value_source`.** The value comes from the first store that has one, in this order: `qcaas`, then the `output_info` value from before the gate, then `ds1`. If no store has one, `value_source` is `none` and the value is NaN, never `0`. `ds1` marks the era-3 rejects that both stores set to null. It's never on an `ok` cell, and its value stays NaN until `decode-ds1` fills it in (section 8, Level 2 step 4).
- **A single `r2`.** It's the served `*_r2`, or `output_info.fit_quality` when there's no served value. The two are byte-identical where both exist.
- **`result_id` and `measured_at`**, which link each cell to its DS1 measurement. `fRB` and `fCX` get a `result_id` per cell. `T1` and `T2` only get one per qubit group in `output_info`, so theirs is `<NA>` until the decode finds the per-qubit id in the DS1 payload and writes it back. (A qubit or two is sometimes dropped from a run, so the group membership isn't always reliable.)
- **`gating_regime`** on the run table (`era0_ungated`, `era1_filter`, `era2_ungated` or `era3_postproc`, section 3). It also tells you where the R² came from: `output_info` for era 1, QCaaS for eras 2 and 3, and nowhere for era 0.

**Filter on `value_status`, not on `value.notna()`.** In Level 2 a present value can still be a reject, so use the status column. For good values, `value_status == "ok"` gives the same rows as Level 1.

> [!NOTE]
> These labels all come from one process, and the collapsible sections below explain it in order. The shared gate checks every cell that has a value or an R². A cell with neither is a padded null. Level 2 resolves it using `output_info`, which needs each run matched to its own file by time. The last collapsible section shows the producer code behind each outcome. You only need these if you work with rejects or nulls.

<a id="the-shared-gate"></a>

<details>
<summary><b>The shared gate</b></summary>

`gate(metric, value, r2, pregated)` holds the physical acceptance rule. It checks, in order:

1. **A served `0` on a fit metric** (`T1`, `T2`, `fRB` or `fCX`) is a placeholder, not a measurement. With no R² it gives `no_result`, and the `0` stays in `value`. The only ones are 19 era-0 `fCX` cells (see *Era-0 zeros* below). With an R² it would give `no_fit`, but none is served, because a failed fit comes as a null value with R² 0 (step 3). On `fRO` a `0` is treated as missing.
2. **`fRO`** is only checked on range, since it never has an R² (section 4).
3. **`r2 == 0`** gives `no_fit`. This is how RB2q marks a failed fit, so in practice it applies to `fCX`.
4. **`r2 < 0.9`** gives `low_r2`. This is checked before range, as in the producer.
5. **A value in range** gives `ok`. Otherwise it's `out_of_range`. `PHYS_RANGE` uses the producer's limits: `(0, 1]` for fidelities, up to 150 µs for `T1` and up to 200 µs for `T2`. (The benchmarking sweep itself only goes to 80 µs.) So `out_of_range` means physically implausible, not just poor. The only difference from the producer is that the gate also excludes an exact `0`, which step 1 already handles. A good R² with no value also ends up here, because era 3 sets an out-of-range value to null but keeps its R².
6. **A value with no R² anywhere** gives `out_of_range` if it's out of range. If it's in range, it gives `ok` only if `pregated` is set, meaning its era's producer already removed fits with R² < 0.9 (in practice, era 1). Otherwise it gives **`no_r2`** (next collapsible section).
7. **Neither a value nor an R²** gives no decision yet. Level 1 drops the cell, and Level 2 resolves it from `output_info` (see *How a null resolves* below).

The build re-applies the gate instead of trusting the served nulls. The producer's gating changed across the four eras (section 3), so served values aren't equally clean. Re-gating removes the era-2 low-R² values that were served unchanged. It also removes the era-1 out-of-range values that the R²-only `filter()` let through.

</details>

<details>
<summary><b>Era-0 zeros (<code>fCX = 0</code> with no R²)</b></summary>

From 2024-05-01 to 2024-06-06 the device ran a local branch of the producer. It filled every benchmark with `0.0` before measuring and wrote `0` for a failed fit. So on these runs a `0` can mean either "not measured" or "fit failed". A successful RB2q fit is at least 0.25. The server run logs show that none of the 19 zero cells in this snapshot was measured: `1-2` and `17-25` (18 cells, 2024-05-28 to 06-06) failed the hardware-model lookup after their direction was flipped, and `10-11` on 2024-05-29 hit an FPGA error. So they're all `no_result`. The main-branch code replaced the branch on 2024-06-06 (`566a830`) and dropped the pre-fill, so later unmeasured couplings are `null`.

</details>

<details>
<summary><b>Values nobody R²-checked (<code>no_r2</code>)</b></summary>

Era 1's producer checked every value's R² against the same 0.9 threshold before upload (`filter()`, from 2024-09-10). The lowest recovered R² on an era-1 `ok` cell is just above 0.9. So an era-1 value with no R² only needs a range check, and that's what `pregated` means. Level 1 can't see the R² (it has no `output_info`), so `r2_pregated(created_at, metric)` sets the flag by date. Level 2 only sets it for cells on runs missing from the `output_info` download, because on a matched run a missing R² is visible directly. The build checks both (`present_no_r2_not_pregated`, `ok_no_r2_not_pregated`).

Every other value without an R² was never checked, so it's `no_r2`. These fall into three groups:

- **Runs before 2024-09-10** (`era0_ungated`). `util.upload_benchmarks` uploaded values without any check, and no `output_info` file covers these runs (section 3). So these cells have neither an R² nor a `result_id`.
- **`T1` and `T2` cells for Q1 and Q2, 2025-04-11 to 2025-07-06.** The maac 1.5.3 upgrade (`58fdd6f`, 2025-04-10) changed the T1 and T2 fit format. After that, the producer's `get_r_squared` crashed right after writing each group's first qubit. That qubit kept its value with an empty `fit_quality`, which `filter()` treated as 1.0 and passed. The rest of the group lost its result (section 7). The fix (`9e51d25`, 2025-04-23) reached the device on 2025-07-07. Level 1 treats `T1` and `T2` between `R2_CRASH` and `UNGATED` as not pregated, which selects exactly these cells.
- **The 2025-11-08 record with no R²** (section 3).

Level 2 keeps the `no_r2` values, so you can still use them based on range alone. For the crash-period cells and a few of the 2025-11-08 cells a DS1 payload exists, so `payload_r2` from `kpi_supplemented` (section 8, Level 2 step 5) gives an R² to gate on. The rest (all of era 0 and most of 2025-11-08) have no R² anywhere.

</details>

<details>
<summary><b>How a null resolves</b></summary>

The producer fills every run with one key per qubit and per coupling in its topology, so a `null` on its own tells you nothing. A padded null with no R² stays `unresolved` until its run is matched to an `output_info` file. Then it becomes one of three states:

- **`low_r2`:** the file still has the value from before the gate (an era-1 fit that failed on R²), so there's a fit to inspect or redo.
- **`no_fit`:** the producer warned that the fit failed and a per-element `result_id` exists, so only the raw shots are left in DS1.
- **`no_result`:** there's nothing, because the group raised an error or the element wasn't measured.

In practice `no_fit` is always `fCX`. It's the only metric that keeps its per-element `result_id` when a fit fails (see the per-metric table). It's also the only one with a failed-fit warning in the downloaded files, `"Did not return a 2Q gate fidelity for C<c>-T<t>"`. For the 1Q metrics a failed fit looks the same as a missing one, so it ends up as `no_result`. Runs before 2024-10-15 have no `output_info` file (section 3), so none of their cells is `no_fit`.

A null `fRO` becomes `no_result`, or `ok` if `output_info` has a value. On a matched run every cell is labelled again using the resolved R² and combined value. All `unresolved` cells are on runs with no `output_info` match. In the same way, era-3 rejects on runs after your `output_info` download ends have no `result_id` and keep `value_source = none`. Downloading more `output_info` resolves both.

**`result_ids` nesting.** Benchmarking files nest by step, then key (`{t1: {group1: id}}`). Calibration files nest by key, then step (`{Q1: {ramsey: [..]}}`, or `{C4_T5: id}` for 2q, which has no step). A few benchmarking files have a 1Q `fit_quality` but no `rb1q` group, so those qubits get an R² but a `result_id` of `<NA>`.

</details>

<details>
<summary><b>Time matching: linking a run across the three stores</b></summary>

Level 2 links the stores by time, in two stages with different tolerances. The producer names the `output_info` file when a run **starts** (`get_output_info_path`) and sets the served `created_at` when it **ends** (`get_benchmark_json`). DS1 records are written as each measurement finishes.

**Stage A: match a served run to its own `output_info` file** (`clean._asof_join_provenance`). Each `created_at` is matched to the latest `output_info` start before it, within `RUN_MATCH_TOLERANCE` (8 h). The gap is the run's own duration. In this snapshot it's at most 6.71 h (median about 3 h), and the closest two served runs are 8.50 h apart. So 8 h is between the two and can't reach the previous run's file. Runs aren't matched by calendar date, because the same evening's file belongs to the *next* run. When a run restarts the same evening (e.g. 2026-03-24 20:48 and 20:51), the later start wins, since that's the run that finished. One matched file, `2026/08/10/13_53_57`, is empty, so its cells become `no_result`. A wider window (16 h or more) only adds the previous run's file, and recovers no extra `fRO`.

**Stage B: check each `result_id` against reused ids** (`recover.resolve_and_guard`, section 8, Level 2 steps 2-5). After a database restore, some ids were reused for different measurements. So each id is looked up in the DS1 spine and kept only if it passes two checks (`measurements.valid_ids`, datasheet 1). The row must have the measurement type that produces the metric (`METRIC_MEASUREMENT_TYPE`), and its `time_start` must be within 24 h of the run start (`ID_TIME_TOLERANCE`). In this snapshot, genuine ids are at most 6.57 h after the run start. Beyond that there's one genuine case 42 days away (a measurement referenced by two files), and then reused ids at 136 days or more. So any tolerance between 12.25 h and 42 days gives the same result, and 24 h leaves plenty of margin. About 8-13% of all `output_info` ids point at the wrong measurement type, and these are the same reused ids. On the `ds1` cells neither check drops anything, so the checks are a safety net, not a real loss.

**The 2025-08-22 08:26:52 re-run.** One file breaks the rule that ids come after the run start. Its ids are up to 12.25 h *before* its file timestamp, because they belong to the previous evening's run (spine ids 218158-218196, 2025-08-21 20:11-23:03). That afternoon (14:47 UTC), `0.4.5` (#24) added `post_process_benchmark` with a bug. Its loop reached `fidelities_ro`, which never has a `fit_quality`, and raised a `KeyError`. This stopped the run before `output_info` was saved. `0.4.6` fixed it at 08:09 UTC the next morning by limiting the loop to `t1s`, `t2s`, `fidelities_1q` and `fidelities_2q`. (The later `#26` and `#27` `None` refactor in `0.4.7` is a separate change.) The file was written again 17 minutes later. It has the timestamp of that re-run's start but points at the evening's measurements. It must have run the fixed code, since it has `fRO` values with an empty `fit_quality.fidelities_ro` (file timestamps are UTC). So the −12.25 h is a one-off, not a clock error.

Stage A's 8 h limits how long a run can take. Stage B's 24 h is a window on both sides of the run start to catch reused ids. They measure different things, so the two tolerances aren't interchangeable.

</details>

<details>
<summary><b>Per-metric producer behaviour</b></summary>

Every `value_status` comes from two producer steps per run. In **Stage 1** (`run_benchmarking`), the producer runs each measurement and stores or skips each element. In **Stage 2** (`post_process_benchmark`, inside `get_benchmark_json`), it gates the values that are left. Stage 1 also decides whether a `result_id` is per group, per qubit or per coupling, so that has its own column. The table describes the current producer code. Before 2025-07-07 a failed `fCX` fit left no value or R² (the served cell is `null`), only the warning and the `result_id`. The exception is 2024-05-01 to 06-06, when the device's local branch wrote `0` (see *Era-0 zeros*). If a group throws after its measurement returns (as in the 2025 `get_r_squared` crash), the group's `result_id` and any values written before the error are kept.

<table>
<thead>
<tr>
<th rowspan="2">Metric</th>
<th colspan="3" style="text-align:center">Stage 1: collection (<code>run_benchmarking</code>)</th>
<th colspan="2" style="text-align:center">Stage 2: post-processing (<code>post_process_benchmark</code>)</th>
<th rowspan="2"><code>result_id</code> per</th>
</tr>
<tr>
<th>fit succeeds (value not <code>None</code>)</th>
<th>fit returns <code>None</code></th>
<th>group measurement throws</th>
<th>R² gate (&lt; 0.9)</th>
<th>range check</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>T1</strong></td>
<td><code>t1s</code>=value, <code>_r2</code>=R², <code>_error</code>=<code>T1_err</code>; warn <em>"Fit quality too low"</em> if <strong>R² &lt; 0.8</strong></td>
<td><strong>key omitted</strong> (no value, R² or error), with warning <em>"Did not return a T1 time"</em></td>
<td>caught (<code>except</code>), so <strong>no <code>result_id</code> and no keys</strong> for the group</td>
<td>value set to <code>None</code></td>
<td>&lt; 0 or &gt; 150 µs: value set to <code>None</code></td>
<td>per <strong>group</strong>, written when the group returns (before the per-qubit fits), so present even if every fit is <code>None</code></td>
</tr>
<tr>
<td><strong>T2echo</strong></td>
<td><code>t2s</code>=value, <code>_r2</code>, <code>_error</code>=<code>T2_err</code>; warn if <strong>R² &lt; 0.8</strong></td>
<td>key omitted, with warning <em>"Did not return a T2e time"</em></td>
<td>no <code>result_id</code>, no keys</td>
<td>value set to <code>None</code></td>
<td>&lt; 0 or &gt; 200 µs: value set to <code>None</code></td>
<td>per <strong>group</strong> (as T1)</td>
</tr>
<tr>
<td><strong>fRB</strong> (RB1q)</td>
<td><code>fidelities_1q</code>=value, <code>_r2</code>, <code>_error</code>=<code>fidelity_uncertainty</code>; warn if <strong>R² &lt; 0.8</strong></td>
<td>key omitted, with warning <em>"Did not return a 1Q gate fidelity"</em></td>
<td>no <code>result_id</code>, no keys</td>
<td>value set to <code>None</code></td>
<td>outside <code>[0,1]</code>: value set to <code>None</code> (rare)</td>
<td>per <strong>qubit</strong>, written <strong>only if</strong> the fidelity is not <code>None</code>, so a failed qubit has <strong>no <code>result_id</code> and no key</strong></td>
</tr>
<tr>
<td><strong>fRO</strong> (ZMap)</td>
<td><strong>only</strong> <code>fidelities_ro</code>=value, with <strong>no R² and no error</strong>; no low-R² warning</td>
<td>key omitted, with warning <em>"Did not return a readout fidelity"</em></td>
<td>no <code>result_id</code>, no keys</td>
<td><strong>excluded</strong>: fRO is never R²-gated</td>
<td>outside <code>[0,1]</code>: value set to <code>None</code> (this can't happen in practice, so a null fRO can only come from Stage 1)</td>
<td>per <strong>group</strong></td>
</tr>
<tr>
<td><strong>fCX</strong> (RB2q)</td>
<td><code>fidelities_2q</code>=value, <code>_r2</code>, <code>_error</code>; sets <code>coupling.quality = int(value*100)</code>. If <strong>R² &lt;= 0.8</strong>: quality set to 50, with warning <em>"Fit quality too low"</em></td>
<td><strong>not omitted: stored as <code>value=0, r2=0, error=0</code></strong>, with warning <em>"Did not return a 2Q gate fidelity"</em> (then R²=0 &lt;= 0.8, so quality is set to 50)</td>
<td>no <code>result_id</code>, no keys (for all of the group's couplings)</td>
<td>value set to <code>None</code> (a failed coupling ends as <code>value None, r2 0, error 0</code>)</td>
<td>outside <code>[0,1]</code>: value set to <code>None</code></td>
<td>per <strong>coupling</strong> (the group's <code>result.id</code>), written <strong>before</strong> the <code>None</code> check, so present even for a failed coupling if the group ran</td>
</tr>
</tbody>
</table>

Two rules apply to every row:

- **There are two thresholds.** The low-R² *warning* is raised at R² < 0.8 (≤ 0.8 for RB2q), but the gate sets values to null at R² < 0.9. So values between 0.8 and 0.9 are set to null without a warning. Label cells on `r2`, not on warnings.
- **Stage 2 only changes the value.** `get_benchmark_json` takes R² and error from before the gate, so a gated cell still has its `_r2` and `_error`. `output_info` saves `fit_quality` (R²) and `result_ids` but not the error. So `*_error` is only in the served QCaaS payload, and a value gated in era 3 is only in DS1.

</details>

## 7. Limitations and caveats

- **Missing isn't zero.** A `null` means the cell was gated, its fit failed, or it was never measured, so drop it. Never treat it as `0`. A served `0` on a fit metric isn't a value either. The only ones are 19 era-0 `fCX` cells whose coupling was never measured (section 6).
- **`fRO` can't be quality-filtered.** It has no R² or error in any era, so it's only range-checked and can't be weighted by uncertainty. Section 6 covers its low-fidelity tail.
- **`fRO` moves in steps of 0.0001.** It's the share of correctly read shots out of 10,000 (5,000 per state), so every value is a multiple of 0.0001 and smaller changes can't show. Some values carry float noise (e.g. `0.9606000000000001`), so round to 4 decimals before comparing them.
- **`no_fit` only exists for `fCX`.** For the 1Q metrics a failed fit and a missing measurement look the same, so both end up as `no_result` (section 6, *How a null resolves*).
- **`no_result` doesn't always mean the fit failed.** Every run from 2025-04-11 to 2025-07-06, and the 2025-11-08 run, has `T1` and `T2` only for Q1 and Q2 (or just Q2) and`fRB` is limited to Q1 and Q2 as well. The DS1 payloads still have fits for the other qubits, so most of those `no_result` cells get a `payload_value` in `kpi_supplemented` (section 8, Level 2 step 5). Up to 2025-07-06 the cause is the producer crash (section 6), and the in-range Q1 and Q2 values from that period are `no_r2`.
- **There's no good fit metric before 2024-10-15.** Era-0 runs have no R² anywhere, so only their `fRO` counts as good. The next run after them is on 2024-10-15 (section 5).
- **The edge set changes.** In this snapshot the directed topology changes twice (2024-05-28 and 2026-03-24), and each change flips the direction of two edges. So match `fCX` to each run's own `topology`, not to a fixed edge list.
- **These are benchmark results, not settings.** There are no `old_*` or `new_*` values, but benchmarking does change two things on the device. ZMap (`update_hw=True`) refits each qubit's `mean_z_map_args` and scores `fRO` against the new map. 2Q RB sets each coupling's `quality` to `int(100·fCX)` if R² > 0.8, and to 50 otherwise. Neither change is stored here, but both appear in the hardware-model snapshots (datasheet 2).

## 8. Access and usage

Both levels from section 6 start from the same raw payloads. Level 1 is built from those alone, while Level 2 also needs two downloads from the calibration server. Neither level reads the other's output, so you can build either one on its own.

### Fetch the payloads (both levels)

Copy `.env.example` to `.env` and set `OQC_AUTH_TOKEN`. `fetch-benchmarking` queries one day at a time, removes duplicates by `created_at`, and saves each unique payload to `data/raw/benchmarking/benchmarking_json/`. It also writes `_manifest.parquet`, which maps every query date to a `created_at` and a `status`. Use it to check cadence and gaps. A rerun queries every date in the range again, but only saves payloads it doesn't already have, and merges the new dates into the manifest.

```bash
fetch-benchmarking                                  # full history (from 2024-05-24, the earliest available)
fetch-benchmarking --start 2026-01-01 --end 2026-08-31
```

### Level 1: no server needed

**1. Build.** `build-benchmarking` flattens the raw payloads, applies the shared gate and writes the Level 1 tables.

```bash
build-benchmarking      # -> data/clean/benchmarking/{runs,kpi}.parquet
```

**2. Load.**

```python
from qpu_drift.sources.benchmarking import load_runs, load_kpi

runs = load_runs()             # one row per run: created_at, topology, has_openpulse
kpi  = load_kpi()              # one row per (run, element, metric): created_at, element,
                               #   element_type, metric, value, error, r2
t1   = load_kpi(metric="T1")   # metric / element are read-time filters
```

### Level 2: needs server access

**1. Download `output_info`.** Copy both server `output_info` folders in full into one local folder. Each holds `YYYY/MM/DD/` date folders, and each date is in only one of the two, so the copies don't overlap. The build reads only the `*_benchmarking.json` files and matches them to runs by time (section 6). So the other routines' files, and benchmarking files from dates with no QCaaS record, don't change the tables. Keep the other routines' files (`calibrate_1q`, `calibrate_2q`, `mixer_calibration`), for analysis if desired (for example `build-run-map` reads them). If you only need Level 2, add `-m --include='*/' --include='*_benchmarking.json' --exclude='*'` to copy just the benchmarking files.

```bash
rsync -a user@lx2:/data/calibration_restore/oqc_calibration_routines/output_info/      data/raw/benchmarking/output_info/
rsync -a user@lx2:/data/calibration_restore/oqc_calibration_routines_data/output_info/ data/raw/benchmarking/output_info/
```

**2. Build.** `build-benchmarking-l2` combines the raw payloads with `output_info`, matching each run to its file by time (section 6). Cells on runs with no matching file stay `unresolved`. The command prints the counts by state and by source. It also writes the list of DS1 payloads for step 3 and prints the rsync command. To do this it needs the measurements index from `build-measurements` (datasheet 1), so build that first. The index is quick to build and uses the same server access as `output_info`.

```bash
build-measurements      # prereq: the measurements index (datasheet 1)
build-benchmarking-l2   # -> data/clean/benchmarking/ (Level 2 tables + DS1 file list)
```

**3. Download the DS1 payloads.** The era-3 rejects are marked `ds1` but their value is `NaN`, because the fitted value is only kept in DS1. Filling them in makes rejected values available for every era, not just before 2025-08-22. Step 2 already wrote the file list (`ds1_fetch.txt`) and a per-cell table (`ds1_manifest.parquet`). `fCX` has one payload per coupling. `fRB`, `T1` and `T2` come from the payloads for the two qubit groups. Each `fRB` cell points to its own group, while `T1` and `T2` cells list both groups' payloads (or one, if `output_info` has only one group's id) and the wrong one drops out at decode. Every listed payload passes the type and time checks in section 6 (time matching). Copy them with the rsync command from step 2:

```bash
rsync -a --files-from=data/clean/benchmarking/ds1_fetch.txt \
    user@lx2:/data/calibration_restore/oqc_calibration_routines_data/calibration_env/ data/raw/benchmarking/measurement_payloads/
```

**4. Decode.** `decode-ds1` reads the payloads, matches them to the `ds1` cells on `(result_id, metric, element)`, and writes the values into `kpi_enriched.parquet`. The file is replaced in one step, so a failed run can't leave it half-written. Rebuilding Level 2 clears these values, so run `decode-ds1` again after step 2.

**5. Supplement (optional).** `supplement-kpi` decodes every downloaded T1, T2echo, RB1q and RB2q payload that passes the checks and writes `kpi_supplemented.parquet`: the enriched table plus `payload_value`, `payload_error`, `payload_r2`, `seed_error`, `error_source`, `rb_lengths`, `in_incident` and `coupling_retired`. It also writes `payload_links.parquet` and `payload_fits.parquet`. `coupling_retired` marks `fCX` rows after a coupling's last RB2q result, but only when DS3 has no later value for it. It reads `kpi_enriched`, so run it again after step 2 or 4.

```bash
decode-ds1             # fills kpi_enriched.parquet in place
supplement-kpi         # optional: writes kpi_supplemented.parquet
```

**6. Load.** The same columns as Level 1, plus `gating_regime` on the run table and `value_status`, `value_source`, `result_id` and `measured_at` on the KPI table.

```python
from qpu_drift.sources.benchmarking import load_runs_l2, load_kpi_l2
from qpu_drift.sources.benchmarking.load import KPI_SUPPLEMENTED_PATH

runs2 = load_runs_l2()                          # + gating_regime
kpi2  = load_kpi_l2()                           # + value_status, value_source, ...
low   = load_kpi_l2(value_status="low_r2")      # metric / element / value_status /
recov = load_kpi_l2(value_source="ds1")         #   value_source are read-time filters
sup   = load_kpi_l2(path=KPI_SUPPLEMENTED_PATH) # + the payload_* columns (step 5)
```

## 9. Open questions

- The QCaaS client only returns records from 2024-05-24, but the server `output_info` has 57 benchmarking runs from 2024-04-15 to 2024-05-18. Does the Postgres database hold anything earlier? Does it offer a server-side summary table that we could copy instead of combining per-run records?
- For the OpenPulse fields, still need to confirm the meaning and units of `constraints`, the `ports` types, and the 8 named `waveforms`.