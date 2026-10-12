# Datasheet 1: Measurements Database

This dataset is a log of the calibration and characterisation runs on the Toshiko LX2 QPU. It has two parts: **(a)** a small SQLite index with one metadata row per run, and **(b)** one gzipped payload file per run on the server, which holds the sweep data, the fits and the fitted results. The routines write it as part of normal operation, not for modelling, so treat it as found data. Most runs target one qubit or one coupling, but some sweep many qubits at once, so the data covers both the whole device and single qubits. This sheet describes what's in the data. Open questions about how it's collected are in section 9.


## 1. Overview

| Field | Value |
|---|---|
| QPU ID | `qpu:jp:3:673b1ad43c` |
| Server root (source of truth) | `/data/calibration_restore/oqc_calibration_routines_data/calibration_env/` |
| Source files | `lite_result_database.db` (the index) and `results/.../*.gz` (the payloads), both under the server root. |
| Payload path | `results/YYYY/MM/DD/HH-MM-SS_<name>.gz`, equal to the row's `relative_filepath`. The time in the file name is `time_end` to the second, not `time_start`. |
| Index | One SQLite table, `result_table`, with 7 columns and one row per run (section 4). About 78 MB in this snapshot. |
| Payload format | A gzipped UTF-8 JSON tree of typed nodes. The size depends strongly on the measurement type (section 4). |
| Time span | Depends on the snapshot. The index starts at a database restore on 2024-09-18, and the snapshot used for this sheet ends on 2026-08-18. |
| Cadence | Event-driven and irregular, not a fixed grid (section 5). |
| Producer | `oqc-calibration-routines` holds the routine scripts. The measurement classes are defined in `oqc-maac`. |
| Unit | One measurement run. `measurement_type` is the measurement class and `measurement_name` is the run label. In this snapshot every type is an `oqc-maac` class except two short-lived test types (`DDropTemp` and `FineFrequencyTemp`, February 2025). |
| Join keys | `id` (the primary key) joins to other tables, and `relative_filepath` locates a row's `.gz` file (section 3). The qubit and coupling tags are in `data_keys`. |
| Device coverage | The 32 active qubits and the device couplings, tagged in `data_keys`. Some couplings are reversed or stop being calibrated during 2026, and a few tags appear only once (section 7). |

Counts in this sheet come from the server snapshot used to write it. A newer snapshot will give different numbers, but the rules stay the same.

## 2. Motivation and intended use

This data exists so that calibration runs can be checked and debugged. Every measurement class in `oqc-maac` runs on the hardware and saves its result. The index row lists the run, and the `.gz` file stores the payload.

Think of the dataset as an irregular **event log** of measurements. Each event has a time, and its payload stores one result per target (a qubit or a coupling). The only exception is `ReadoutMatrix`, which stores its results by readout state (section 7). "Per qubit" describes the results, not how the run worked. One run can sweep a whole group of qubits at once (section 3), and its payload then holds a separate result for each qubit (`data["Q1"]`, `data["Q4"]`, ...).

To use the data, filter it to the measurement type your question needs (section 4 shows what each type stores), for example `Ramsey` for qubit frequency, `T1` for relaxation or `RandomizedBenchmarking2q` for gate fidelity. Then arrange those events into the time series or groups you need.

## 3. Provenance and collection

Every measurement run writes one index row and one `.gz` payload, and both have the same `measurement_name` and times. The payload file is named after the row's `time_end`, to the second. All times come from the server's clock, which is UTC (section 7).

`oqc-maac` saves the payload twice: once before the index row exists (`id: null`), and again with its `id` after processing. If processing fails, the first version stays, with `id: null` and only `raw_output` per target (about 200 `CRUnitaryIZ` payloads in this snapshot). Every other payload repeats its row's `id`, so match a payload to its row by `relative_filepath` for these.

Most runs come from **two scheduled calibration windows a day**:

- **Morning (from about 08:45)** runs 1q calibration, then 2q calibration.
- **Evening (from about 19:05)** runs mixer calibration, 1q, 2q and then benchmarking (`calibrate_and_benchmark.py`). So the coherence and fidelity benchmarks (`T1`, `T2echo`, `RandomizedBenchmarking1q` and `RandomizedBenchmarking2q`) almost all run in the evening, occasionally past midnight.

Some runs fall outside these windows. In October 2024 a `cr_u_stability` test ran 2q calibration around the clock for about five days in total (18 to 21 October and 25 to 28 October). Apart from that test, only a few 2q runs start at night or in the afternoon, and ad-hoc runs happen at any hour. So take run membership from the run map (`build-run-map`), not from the time of day.

One-qubit routine runs sweep one of **two fixed qubit groups**, `group1` and `group2`, named in the run label (e.g. `xpi2_amp_group1`). The groups are the two colours of the coupling graph, so no two qubits in a group share a coupling. A run usually sweeps only part of its group, because the routine skips some qubits by design and drops any that fail an earlier step. So **read the swept qubits from `data_keys`, not from the label**. Two-qubit routine runs always target one coupling (`cr_u_C{c}-T{t}`, `rb2q_C{c}-T{t}`, ...). Bare names such as `rabi`, `ramsey` or `criz` mark ad-hoc runs.

<details><summary><b>Timing, group and run-name details</b></summary>

- **Payload times.** `oqc-maac` writes payload times to the whole second, so the payload `time_start` is the index value with the fraction of a second dropped.
- **Window sizes.** In this snapshot, by far the most rows start between 19:00 and 21:00, followed by 08:00 to 10:00. Between 95% and 98% of each benchmark type's rows start between 19:00 and 01:00. Off-window `calibrate_2q` runs start in every hour outside the windows, but most belong to the October 2024 `cr_u_stability` test.
- **Partial groups.** In this snapshot, a run sweeps its whole group in about 44% of `group1` rows and 52% of `group2` rows. Qubits with an active frequency-shift channel (e.g. Q2, Q13) skip Ramsey, FineFrequency and DRAG. DRAG also skips any qubits in an optional `skip_drag` list in `measurement_env.json` (absent in this snapshot). Qubits that fail an earlier FineFrequency, amplitude or DRAG step are dropped (`calibrate_1q.py` lines 190, 240 and 265, as of 2026-08-18).
- **Stray rows.** Each label sweeps the same set of qubits every month. The exception in this snapshot is 40 rows (2024-10-18 and 2024-10-21, plus two `zmap_group1` rows on 2025-02-14) that also include qubits from the other group. The full group lists are in the `measurement_env.json` details below.
- **Two-qubit names.** Routine runs are `cr_u_C{c}-T{t}`, `cr_u_stabilityC{c}-T{t}`, `rb2q_C{c}-T{t}` and `ib2q_C{c}-T{t}`. Ad-hoc runs use bare names (`criz`, `cr_iz`, `cru`, `crleak`, ...).
- **Ad-hoc one-qubit runs** (`rabi`, `xamp`, `ramsey`, ...) usually target a single qubit and sometimes a whole group. Once in this snapshot, a `rabi` run targets all 32 qubits.

</details>

### Accessing the server

You reach the data over SSH on the LX2 server. The Platform Engineering team can set up access, or make a copy if you need to change data in place. The index and its payloads sit in one folder, next to a few config files that add useful context:

```
/data/calibration_restore/oqc_calibration_routines_data/calibration_env/
├── lite_result_database.db               # the index
├── results/                              # the .gz payloads, one per index row
│   └── YYYY/MM/DD/HH-MM-SS_<name>.gz     # path == result_table.relative_filepath
├── calibrations/                         # dated hardware-model snapshots (datasheet 2)
│   └── YYYY/MM/DD/HH-MM-SS_calibration.gz
├── calibration.json                      # current live hardware model
├── calibration_backup*.json              # hardware-model backups at milestones
├── calibration_pre_*.json                # more hardware-model backups
├── measurements/                         # one saved config per named measurement
│   └── <name>.json
├── measurement_env.json                  # environment config the runner loads at startup
├── config.toml                           # runtime config
├── logs/                                 # only one run's purr log, not a log history
└── mixer-test.ipynb, testCal.json, .ipynb_checkpoints/   # ad-hoc work
```

`measurements/` holds the saved sweep settings and defaults for each named measurement, not results. `measurement_env.json` is the config the runner loads at startup. It's also a useful map of the device, because it lists the active qubits, the couplings and the two qubit groups.

> [!NOTE]
> **Plaintext credentials.** `measurement_env.json` and `config.toml` contain plaintext credentials. Don't copy them into the repository or share them.

<details><summary><b>What's in <code>measurements/</code> and <code>measurement_env.json</code></b></summary>

- **`measurements/`.** Each file is `{type:"Measurement", name:<class>, dict:{name, env, default_config:{...}}}`. `default_config` holds `shots`, `rep_period`, `update_hw` and the sweep ranges. The file list matches the `measurements` array in `measurement_env.json`.
- **`measurement_env.json` keys.** `qpu_id`, `description` and `release_number`, the control hardware `address` and `port`, the QSLAM service `host`, `q` (the 32 active qubits), `q_pairs` (the 32 couplings as `[control, target]`), `q_groups`, `q_pair_groups` (28 of the 32 couplings, each in its own group), `measurements` (the 197 named measurements), the `live_cal` path, `slack_channel`, `use_qslam`, and two credential blocks: `db_info` and `blob_store`. Any other key is passed to the routine scripts as an optional setting. For example, `calibrate_1q.py` reads a `skip_drag` list of qubits to leave out of DRAG calibration.
- **`q_groups`:**
  - `group1` (17 qubits): 1, 4, 6, 9, 11, 13, 15, 16, 18, 20, 21, 23, 25, 27, 31, 33, 35
  - `group2` (15 qubits): 2, 5, 7, 10, 12, 14, 17, 19, 22, 24, 26, 28, 30, 32, 34

</details>

## 4. Composition and structure

**Format and encoding:**
- The index is a single SQLite table. `time_start` and `time_end` are stored as ISO-8601 **text** with no timezone.
- Payloads are gzipped UTF-8 JSON (maximum compression). They aren't flat records. Each one is a **custom tree of typed nodes** (each node is `{type, ...}`), not standard `jsonpickle`.
- Numeric arrays inside a payload aren't plain JSON numbers. Each one is a `numpyarray` node, usually `{type, dtype, shape, value}`, where `value` holds the raw array bytes as a hex string. `struct` or `numpy.frombuffer` decodes it (section 8). Payloads up to 2024-12-09 use a plain `{type, list}` form instead, so a reader must handle both.
- Payload size depends strongly on the type. One-qubit types such as `Ramsey` are about 11 KB gzipped and 95 KB decompressed (median). Types that store spectra or single shots (`MeasureBands`, `RandomizedBenchmarking2q`, `ZMap`) are tens to hundreds of times larger, up to about 50 MB decompressed. The rare `InterleavedBenchmarking2q` payloads are the largest, up to about 210 MB.

**Index schema**

| Column | Type | Meaning | Example |
|---|---|---|---|
| `id` | INTEGER PK | Measurement id, numbered from 1 with no gaps. Repeated in the payload, except where processing failed (section 3). Not in time order (note below). | `1` |
| `measurement_name` | VARCHAR | Run label, often `..._group1`, `..._group2` or `cr_u_C{c}-T{t}` | `measure_bands_before_cal` |
| `measurement_type` | VARCHAR | Measurement class name | `MeasureBands` |
| `time_start` | DATETIME (text) | Run start | `2024-09-18 11:53:21.161967` |
| `time_end` | DATETIME (text) | Run end | `2024-09-18 11:53:21.601072` |
| `relative_filepath` | VARCHAR | Path to the `.gz` payload under the server root | `results/2024/09/18/11-53-21_measure_bands_before_cal.gz` |
| `data_keys` | VARCHAR | Target tags, separated by `&` | `&Q1&`, `&C4-T5&`, `&Q2&&Q5&...` |

> [!NOTE]
> **Ids aren't in time order.** A `MixerCalibration` row gets its id after its sub-steps finish, but it starts before them. So when rows are sorted by `id`, every `MixerCalibration` row in this snapshot starts before the row above it. Sort by `time_start` to get time order.

**Measurement families**

The `measurement_type` values fall into five families by what they measure. Most of the log is maintenance: in this snapshot the mixer types alone make up about two thirds of all rows.

| Family | What it measures | Share of rows (this snapshot) |
|---|---|---:|
| Mixer maintenance (`MeasureBands`, `LOLeakageOptimisation`, `SidebandImbalanceOptimisation`, `SidebandPhaseOptimisation`, `MixerCalibration`) | IQ-mixer tuning on the drive and measure channels (LO leakage, sideband phase and imbalance), plus spectrum-analyser checks. `MeasureBands` is a before-and-after check, not a tuning step. | about 64% |
| Two-qubit (`CRUnitaryIZ`, `RandomizedBenchmarking2q`, `CRUnitaryTomography`, `CrossResonanceRabi`, `CrLeakage`, `CRDrag`, ...) | Cross-resonance (entangling) gate characterisation | about 27% |
| One-qubit physics (`XPi2Amp`, `XPi2Drag`, `FineFrequency`, `Ramsey`, `Rabi`, `FineFreqShift`, `T1`, `T2echo`, `RandomizedBenchmarking1q`, ...) | Drive amplitude, frequency and coherence | about 8% |
| Readout (`ZMap`, `ReadoutMatrix`, `ReadoutAmp`, `ReadoutWidth`, `ReadoutFrequency`, `ZMapDDrop`) | Readout discrimination and fidelity | about 2% |
| Spectroscopy, search and other (`ResonatorSpectroscopy`, `*PeakFinder`, `ResonatorChiMeasurement`, ...) | One-off tune-ups and diagnostics | under 0.1% |

**Payload shape**

Every measurement type uses **the same outer structure**: a nested tree of `{type, name, dict}` nodes that `gzip` and `json` can read on their own. There's no `jsonpickle` and, unlike the hardware models in datasheet 2, no `py/id` references to resolve. Types differ only in the contents of the innermost nodes. Every payload has three levels:

- **Level 1, run header** (`dict`): `id`, `measurement_name`, `time_start` and `time_end`, copied from the index row (`time_start` is cut to the whole second and `id` is null where processing failed, section 3). It wraps the two blocks below.
- **Level 2, `config`**: the sweep setup. `shots` and `rep_period` appear in every type. `pts`, ranges and `q` appear where the type uses them, and two-qubit types target a `q_pair` instead of `q`. Tuning types also store their starting values as `old_*`, one per target (`old_frequencies: {Q1: ..., Q4: ...}`). These are read from the live hardware model when the run is set up.
- **Level 3, `data`**: one node per target, keyed `Q{n}` or `C{c}-T{t}`, each `{type: "Data", name, dict: {raw_output, <fit(s)>, new_*, ...}}`.

```
{
  type: "Result",
  name: <MeasurementType>,
  dict: {                                    # LEVEL 1: run header
    id,                                      #   == result_table.id (null if processing failed)
    measurement_name,                        #   == result_table.measurement_name
    time_start, time_end,                    #   match the index row
    config: {                                # LEVEL 2: sweep setup + starting point
      type: "Config", name: <MeasurementType>,
      dict: {
        shots, rep_period, pts, <ranges>, q,       # sweep setup (q_pair for 2q types)
        old_<param>: { Q1: ..., Q4: ... }          # pre-measurement values (tuning types only)
      }
    },
    data: {                                  # LEVEL 3: result, one node per target
      "Q1": { type: "Data", name, dict: { raw_output, <fit(s)>, new_<param>, ... } },
      "Q4": { ... },
      ...                                    #   keyed by Q{n} or C{c}-T{t}
    }
  }
}
```

**What changes between types:**
- **Only tuning types have `old_*` and `new_*`.** Types that set a parameter store both the starting value and the result, so you can read a run's change from its file alone. Characterisation types store only their fit and fitted value. A few types use their own layout (details below).
- **Fit nodes differ in name and number.** Most types have one `fit`, the ladder types (section 7) have a `fits` list, and `Rabi` has two fits. The CR unitary types store tomography values instead of a fit.
- **The fit format changed during the log.** Newer fits include an `r2` and older fits don't. The switch date depends on the type (details below).
- **`raw_output` differs in shape.** Most types store an averaged sweep trace. `ZMap`, the readout scans and `RandomizedBenchmarking2q` store single shots. `MixerCalibration` and `MeasureBands` have a `raw_output` key, but it's null.

<details><summary><b>Per-type payload details</b></summary>

- **Tuning types** (with `old_*` and `new_*`): `Ramsey`, `Rabi`, `XPi2Amp`, `XPi2Drag`, `FineFrequency`, `FineFreqShift`, `ZMap`, `CRUnitaryIZ`, `CRUnitaryTomography`, `LOLeakageOptimisation`, `SidebandImbalanceOptimisation` and `SidebandPhaseOptimisation`. Several rare types (e.g. `Rabi12`, `CRDrag`, the readout scans and resonator spectroscopy) also store both, and a few store only one side. Some `old_*` entries are null.
- **Other layouts.** `T1`, `T2echo`, `RandomizedBenchmarking1q` and `RandomizedBenchmarking2q` store only their fit and fitted value. `MeasureBands` stores a diagnostic spectrum and the band levels read from it, with no fit. `ReadoutMatrix` stores a state matrix. `MixerCalibration` puts its sub-step results (each with its own `old_*` and `new_*`) in `results` and `measurements` lists instead of at the top level.
- **Fit nodes.** One `fit` for `Ramsey`, `T1`, `T2echo`, the two RB types and the mixer sub-steps. A `fits` list for the `FineFrequency`, `FineFreqShift`, `XPi2Amp` and `XPi2Drag` ladders. `raw_fit` and `signal_fit` for `Rabi`. `histo_fits` for `ZMap`, which is null in the payloads checked.
- **Fit format.** There are two formats, so a reader must handle both.
  - **Older:** `Fit` nodes with `function_string`, `opt_vector`, `cov_matrix` and the parameter names in `variable_parameters`. No `r2`.
  - **Newer:** `FittingBase` nodes with `function_string`, `optimised_results`, `covariance_matrix` and `r2`. The parameter names are in `variables.list`, or in the keys of `_variables.parameters` from late May 2026 (and briefly in February 2026), so check both.
  - **Switch dates:** 2025-03-20 for the one-qubit tuning types, 2025-04-11 for `T1` and `T2echo`, and 2025-11-08 for the two RB types. The mixer sub-steps use `FittingBase` throughout.
- **`raw_output` shapes.** `(pts,)` for `Rabi`, `Ramsey`, `T1` and `T2echo`. `(2, pts)` for the ladder types. `(lengths, seeds)` for `RandomizedBenchmarking1q`. `(lengths, seeds, 2, 1000)` for `RandomizedBenchmarking2q`. `(pts, 2, shots)` single shots for the readout scans. `ZMap` stores `{g, e}` with 5,000 single-shot IQ points per state and has 19 fields per target. The CR types store one value per tomography basis (`z-z`, `z-x`, `x-z`, ...) for each control state.
- **Class names.** Some subclasses store the parent class as the payload `name` (e.g. `ZMap` for `ZMapDDrop`), so use the index `measurement_type` to identify the type.

</details>

## 5. Distribution and statistics

- **Runs are short and irregular.** Runs happen when a routine or a person starts them, not on a fixed grid. Most take seconds, and a few take over an hour.
- **A few types make up most of the log.** `CRUnitaryIZ`, `MeasureBands`, `LOLeakageOptimisation`, the sideband and mixer types, and `RandomizedBenchmarking2q` run tens to over a hundred times a day. Per-qubit physics is sparse by comparison. In this snapshot, `XPi2Amp` runs about 15 times a day, `Ramsey` and `Rabi` about 4 times, and `T1`, `T2echo` and `RandomizedBenchmarking1q` about twice.
- **Types are either routine or one-off.** Routine types run on almost every day. The rest run on a handful of days at most, as ad-hoc tune-ups and diagnostics. Even routine types don't cover every qubit every day.
- **Not every evening has a benchmarking run.** The benchmarks run at the end of the evening window (section 3), so there's usually one benchmarking pass an evening. Some evenings have none, because the evening window never ran or stopped before benchmarking. In this snapshot that's about 7% of evenings (section 9).
- **Day size varies with activity.** A typical day has several hundred rows. The first day (2024-09-18) is mostly mixer and `MeasureBands` runs from the initial setup, and the last day of a snapshot is usually partial.
- **Runs follow a fixed order within a window**, which matches the routine scripts. In this snapshot over 99% of the 1q, mixer and benchmarking runs follow it. The only two runs out of order are mixer runs on 2026-02-25 and 2026-02-26, which measure the bands again before calibrating. The time between windows and between days isn't fixed, so use the timestamps instead of assuming a schedule.

<details><summary><b>Numbers for this snapshot</b></summary>

- **Span and size.** 444,080 runs from 2024-09-18 11:53 to 2026-08-18 09:12 (about 23 months), on 649 days with any row. The median day has 667 rows. 2024-09-18 has 1,101 and 2026-08-18 has 79.
- **Durations.** Median about 9 s, 95th percentile about 70 s, maximum about 4,900 s.
- **Rows per day** (averaged over the 649 days with any row). `CRUnitaryIZ` about 152, `MeasureBands` and `LOLeakageOptimisation` about 125 each, the three sideband and mixer types about 63 each, and `RandomizedBenchmarking2q` about 28.
- **Routine types.** 17 of the 42 types run on 602 to 641 days (`CRUnitaryIZ` the most). The other 25 run on 14 days or fewer.
- **Evenings without a benchmarking run.** Counted per evening (06:00 to 06:00) from the run map, outside outages and DS1 gaps: 45 of the 645 evenings from 2024-10-15 to 2026-08-17 (7%). 3 have no rows at all, 17 have no evening rows, and 25 have evening rows but no benchmarking. On 4 evenings benchmarking ran twice.
- **Count per evening, not per `date`.** Counting the dates with no `T1`, `T2echo` or RB row finds only 38 such days, because runs that cross midnight and ad-hoc `T1` or RB rows make a day look covered.

</details>

## 6. Preprocessing and cleaning

The index is already tidy, so cleaning is light. The build ([clean.py](../../src/qpu_drift/sources/measurements/clean.py), run with `uv run build-measurements`, section 8) keeps every row and never changes the raw database or the payloads. It only reads the index, and opens it read-only.

### Step 1: build the measurement spine

1. **Load** `result_table` unchanged.
2. **Parse the times.** `time_start` and `time_end` become datetimes, and `duration_s` is added. They keep no timezone, but the clock is UTC (section 7).
3. **Parse the targets.** `data_keys` is split into a qubit list (`&Q1&&Q4&` becomes `[1, 4]`) and a coupling list (`&C4-T5&` becomes `["C4-T5"]`). Each row gets a `target_type` (`qubit`, `coupling` or `none`), so rows without a target are flagged, not dropped.
4. **Add helper columns:** `date`, `month`, `week`, and a `tier` that sorts the types into `mixer_hygiene`, `two_qubit`, `one_qubit_physics`, `readout`, `spectroscopy` and `diagnostics`.
5. **Check the result** with `validate_spine`. It counts missing times, negative durations, duplicate ids, rows with both qubit and coupling targets, and types with no tier. It only warns and doesn't stop the build. All counts are zero in this snapshot.
6. **Write the outputs** to `data/clean/measurements/`: the full `measurements.parquet`, plus `measurements_by_qubit.parquet` and `measurements_by_coupling.parquet` with one row per measurement and target.

Every index row becomes a spine row. The rows with no target are the `ReadoutMatrix` runs, which are keyed by readout state, plus one empty `Ramsey12` in this snapshot. Their qubits are only in the payload, so they appear in the spine but not in the per-target tables. `coverage()`, `cadence()`, `device_epochs()` and `monthly_mix()` compute summaries from the spine. `cadence()` measures the gap between consecutive rows, so for types that repeat within a run (the ladders) it reports the gap between steps, not between runs.

> [!NOTE]
> **Payloads aren't cleaned.** There's one per row, each type has its own layout, and an analysis usually needs only one type, so it's simpler to decode them when needed. In this snapshot they're about 134 GB gzipped and 825 GB decompressed.

### Step 2: rules for analysis

The build doesn't apply these rules. They're choices you make when you load the data.

- **Group by `measurement_type`, not `measurement_name`.** A name mixes the batch with the measurement (`xpi2_amp_group1`).
- **Group by run, not by `date`.** A run that crosses midnight UTC puts its later rows on the next `date`. The spine has no run column. To add one, left-merge `load_run_rows()` from the run map on `id`, which gives each row's `routine`, run `start` and `claimed` flag. `claimed` is True when the run's `output_info` file lists the row's `id`, and False when the row was placed in the run by its time. Rows in no run are kept, but these three columns are empty. They're mostly ad-hoc work, plus a few routine-named rows that fall outside every run's span.
- **A missing `r2` doesn't mean a bad fit.** The older fit format has no `r2` (section 4), so filtering on `r2` drops every older fit of a type. Use the fit's uncertainty (`error`, section 8) or whether a routine claimed the result instead.
- **Match claims per target.** A row is "claimed" when a routine run stored its id. Get the claimed ids from the run map with `load_run_results()` and keep `valid` ids only. Some steps store an id only for the targets they accept, so a claimed row can still include rejected targets. This applies to the `calibrate_1q` `amp`, `freq`, `drag` and `freq_shift` steps, where a rejected `new` value never reached the hardware, and to benchmarking `rb1q`, where a rejected qubit returned no fidelity. For those, join on both `result_id` and `key`. The keys look like `Q1`, the same as the payload targets, so convert them with `ids.qubit()` to match the spine's `qubits`.
- **Only routine types are claimed.** The mixer sub-steps are claimed through their parent `MixerCalibration`. Spectroscopy, ad-hoc and readout types (except `ZMap`) are never claimed, so use the claim filter on routine types only.

<details><summary><b>Claim details</b></summary>

- **Rejected targets in claimed rows.** In this snapshot, the step rejected 8.2% of the qubit results in claimed `XPi2Amp` rows, 1.9% in `FineFrequency` rows, 1.2% in `XPi2Drag` rows, and under 1% in `FineFreqShift` and `RandomizedBenchmarking1q` rows. The benchmarking `t1`, `t2e` and `zmap` claims are stored per group, so match those on the id alone.
- **Claimed doesn't always mean applied.** `mixer_calibration` stores the `MixerCalibration` id even when the fitted imbalance is outside 0.7 to 1.3. In that case it resets the channel's imbalance to 1 and its phase to 0, but keeps the new DC bias values (`check_bad_mixer_calibration` in `mixer_calibration.py`). The run map can't show this, so check the fitted imbalance or the hardware model.
- **Mixer sub-steps.** 99.9% of LO-leakage and sideband rows fall inside the run time of a parent `MixerCalibration`, and 99.6% of those parents are claimed.
- **Claim rates.** Besides the mixer sub-steps, 24 types are never claimed (readout types except `ZMap`, spectroscopy and ad-hoc types). `ZMap` is 92% claimed. `CRUnitaryIZ` is 60% claimed by design, because the N = 2 step replaces N = 1 (section 7).
- **Midnight.** 306 claimed rows start on a later `date` than their run. Another 39 start on the day before, because one benchmarking file (2025-08-22 08:26) is dated about 9 to 12 h after its rows.

</details>

## 7. Limitations and caveats

- **Times are UTC with no timezone.** This holds for both the index and the payloads, because the gzip header times match the file names read as UTC. Add `tz="UTC"` before joining to other sources such as DS4.
- **`data_keys` uses three formats.** Qubits are `&Q1&` and couplings are `&C{c}-T{t}&`. `ReadoutMatrix` uses readout-state labels (`&00&&01&&10&&11&`, `&0&&1&`) with no qubit at all. A parse that only looks for `Q` tags misses every coupling and readout row, which is over a quarter of rows in this snapshot. The spine parses couplings as well as qubits.
- **Couplings are directional, and the set changes in 2026.** `C1-T2` and `C2-T1` are different couplings, so don't merge them. On 2026-03-24, C1-T2 and C1-T10 are replaced by C2-T1 and C10-T1. Six more couplings stop being calibrated between March and July 2026. Daily `cr_u` and `rb2q` counts drop at those dates because there are fewer couplings, not because runs failed.
- **Claimed ladder steps aren't all the steps that ran.** `calibrate_1q` and `calibrate_2q` repeat some steps with more pulses each time (a ladder, e.g. `XPi2Amp` with N = 1, 2, 4, 8), and they store only the steps they keep. So a run often has more rows in the index than claimed ids. Count the steps from the index, and group them by each payload's own `N` or `delay`.
- **`RandomizedBenchmarking2q` sequence lengths depend on the previous result.** Treat `lengths` as an outcome of the previous run, not as a fixed setting.
- **`id` is only unique within one snapshot.** An id from another source or an older snapshot can point at a valid but different row. Check it before you use it (section 8).

<details><summary><b>Caveat details</b> (missed rows, ladders, sequence lengths, coupling changes)</summary>

- **Rows missed by a `Q`-only parse.** In this snapshot, 117,856 coupling rows, 643 readout-state rows and one `Ramsey12` with an empty `data_keys`. One malformed tag (`&QQ14&`, 2 rows) still parses correctly to Q14.
- **Ladders.** `calibrate_1q` stores a step's id only for the qubits it accepts, drops a qubit at its first rejected step, and ends the ladder when no qubits are left. `calibrate_2q` runs `CRUnitaryIZ` with N = 1 and then N = 2, and stores the last step that wasn't reverted. So a qubit with claimed `XPi2Amp` steps at N = 1, 2 and 4 but not N = 8 usually failed the N = 8 check, and a claimed `CRUnitaryIZ` N = 1 row usually means the N = 2 step was reverted.
- **Sequence lengths.** `benchmarking.py` (lines 246 to 249, as of 2026-08-18 - when this snapshot was taken) uses `range(1, 15)` when any coupling quality in the run's pair group is below 92, and the 17-length list from line 165 otherwise. Each quality is set from the previous run's fCX, or to 50 when the fit quality is 0.8 or less (lines 270 to 273). The rule matches every payload checked.
- **Coupling changes.** C1-T10 has its last three `cr_iz` rows on 2026-03-25 (10:43 to 10:44). The one-off tags are C23-T24 (3 rows) and C9-T12 (1 row). The six couplings that stop are C2-T9, C26-T35, C19-T11, C22-T18, C10-T11 and C18-T14, with their last `CRUnitaryIZ` row between 2026-03-28 and 2026-07-15. The `calibrate_2q` `output_info` still lists them, with old values, no new values and no result id. Typical daily `cr_u` and `rb2q` counts fall in steps, from 128 and 32 before 2026-03-24 to 104 and 26 after 2026-07-15.

</details>

## 8. Access and usage

The spine is the main entry point. It already has the parsed `qubits`, `couplings`, `target_type`, `date` and `relative_filepath` columns, so most analysis needs nothing more. Decode the `.gz` payloads only when you need the fitted values.

### Build and load the spine

**1. Build.** The build needs a local copy of the server index. Download `lite_result_database.db` with `scp` (section 3) and save it as `data/raw/measurements_index_<date>.db`. The file is large and belongs to the server, so it isn't committed. Then run:

```bash
uv run build-measurements                # uses the newest data/raw/measurements_index_*.db
uv run build-measurements --raw path.db  # or a specific snapshot
```

**2. Load.**

```python
from qpu_drift.sources.measurements import load_measurements

m = load_measurements()   # data/clean/measurements/measurements.parquet
```

### Fetch and decode payloads

The decoder runs locally, because `numpy` isn't installed on the server. So you first copy the payloads you need, using the spine's `relative_filepath` values.

**1. Select the runs** and write their paths to a list.

```python
spine = load_measurements(measurement_type="Ramsey")
spine = spine[spine["date"].astype(str) == "2026-06-09"]
spine["relative_filepath"].to_csv("to_fetch.txt", index=False, header=False)
```

**2. Download them.** `--files-from` reads each path in the list relative to the server root, and recreates the same folders under the local folder.

```bash
SERVER=/data/calibration_restore/oqc_calibration_routines_data/calibration_env
rsync -a --files-from=to_fetch.txt user@lx2:"$SERVER"/ data/raw/measurement_payloads/
```

> [!NOTE]
> **Where to save payloads.** Any local folder works. Don't use the DS3 payload folder (`data/raw/benchmarking/measurement_payloads/`), because the DS3 recovery steps manage it.

**3. Decode.** Each row's `relative_filepath` now points to a file under the local folder.

```python
from pathlib import Path
from qpu_drift import decode

local_root = Path("data/raw/measurement_payloads")
objs = (decode.payload(local_root / fp) for fp in spine["relative_filepath"])
values = decode.results_frame(objs)   # one row per measurement, target and parameter
```

The decoder ([decode.py](../../src/qpu_drift/decode.py)) turns each payload into rows of `(id, measurement_type, target, param, old, new, r2, fit_model, error)`, which join to the spine on `id`. This frame is a summary. It keeps each `new_*` value with its `old_*` value, the first fit's model and `r2`, and an uncertainty (`error`) where the type has one. `decode.payload()` returns the full tree, so nothing is lost at the source. Save any tables you want to keep in `data/derived/measurements/`, not `clean/`.

> [!NOTE]
> **When you need more.** The frame covers the common cases. The collapsibles below explain what it leaves out, how to add your own reader for a type, and how to check an id that comes from another source. You only need them if the frame is missing something or you're looking up ids from [DS3](03_benchmarking_files.md).

<details><summary><b>What the frame keeps and drops</b></summary>

- **Kept.** Each `new_*` value with its `old_*` value from the config, plus the first fit's model string (`fit_model`) and `r2`. `T1`, `T2echo`, the two RB types and `ZMap` give one KPI value per target as `new`, with no `old`. For `ZMap` this replaces its tuning `old_*` and `new_*` values, so read those from the payload. For the ladder types, `r2` is the lower of the two line fits. `r2` is `None` for the older fit format, which has none (section 4).
- **Dropped.** The full parameter vector, the covariance matrix, the raw traces and any other fits. Types with no `new_*` (e.g. `MeasureBands`) become one row per target with no value.
- **`error`.** The shared reader (`extract_generic`) handles most types and leaves `error` empty. Some types have their own reader. `T1`, `T2echo` and the two RB types read their KPI value and its uncertainty. `ZMap` reads its readout fidelity, which has no uncertainty. `Ramsey` reads the standard deviation of its fitted frequency, and the four ladder types read the standard deviation of the point where their two lines cross. All other types have `error = None`.
- **Raw arrays.** `decode.array` turns one `numpyarray` node into an `ndarray`, in either stored form (section 4). `decode.plain` converts a whole subtree.

</details>

<details><summary><b>Adding a reader for a type</b></summary>

Add a reader when `oqc-maac` gains a new measurement type, or when you need fields the shared reader can't find, such as extra fit parameters or per-shot IQ data. `decode.register_extractor(<measurement_type>)` adds your function to a `{measurement_type: function}` table. `extract()`, and so `results_frame()`, uses it when the payload's type matches, and uses the shared reader otherwise. Import your module before decoding so that the reader is registered.

```python
from qpu_drift import decode
from qpu_drift.decode import Result, iter_targets, read_fits

@decode.register_extractor("Rabi")            # keyed by measurement_type; replaces any existing reader
def _rabi(obj) -> list[Result]:
    rows = decode.extract_generic(obj)        # keep the old/new rows
    for target, node in iter_targets(obj):    # each Q / coupling
        for fit in read_fits(node):           # raw_fit and signal_fit, both formats
            rows.append(Result(obj["dict"]["id"], "Rabi", target, param="fit_params",
                               old=None, new=fit.params, r2=fit.r2,
                               fit_model=fit.model))
    return rows
```

The step 3 `results_frame` call stays the same, and returns the extra rows for that type. To get one payload's `Result` objects instead of a frame, call `decode.extract()` directly:

```python
results = decode.extract(decode.payload(local_root / fp))   # list[Result], chosen by type
```

</details>

<details><summary><b>Checking an id from another source</b></summary>

Within one snapshot, each row's `id` matches its payload's `id`, except for the failed payloads with `id: null` (section 3). But `id` isn't a stable global key. The index is a restored database, and its ids start from 1 at the restore on 2024-09-18. So an id from another source or an older snapshot, such as a `result_id` in the [DS3](03_benchmarking_files.md) `output_info` files, can point at a different measurement. Check the row before decoding with `valid_ids(result_id, expected_time, spine, expected_type=None)` in [clean.py](../../src/qpu_drift/sources/measurements/clean.py). It keeps an id only if its row starts within `ID_TIME_TOLERANCE` (24 h either side) of the expected run start. If you pass `expected_type`, the row must also have that `measurement_type`.

In this snapshot, genuine ids start between 0 and 6.57 h after their run start. One morning benchmarking file (2025-08-22 08:26:52) is the exception. Its ids belong to the previous evening's run, between 12.25 h and 9.39 h earlier ([DS3 section 6](03_benchmarking_files.md#6-preprocessing-and-cleaning) explains why). Reused ids sit 136 to 183 days away, and the same file repeats one id from 42 days earlier. So the 24 h window keeps every id from the run itself and rejects both kinds of wrong id.

</details>

## 9. Open questions

- **Evenings without a benchmarking run.** On these evenings the window either never ran or stopped before benchmarking (section 5) - why?
