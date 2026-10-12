# Datasheet 2: Hardware Models

This dataset is the **applied control state** of the QPU. One file is a full snapshot of the device: every qubit and coupling, with the pulse parameters, channel wiring, readout discrimination, error-mitigation matrices and coupling metadata in use at that moment. The calibration routines save these files to run the device, not for analysis, so treat it as found data. This sheet describes what's in the files; open questions about how they're produced are in section 9.


## 1. Overview

| Field | Value |
|---|---|
| QPU ID | `qpu:jp:3:673b1ad43c` |
| Source name | Hardware model |
| Source of truth | The dated server archive, `calibration_env/calibrations/YYYY/MM/DD/HH-MM-SS_calibration.gz`. It's large (about 0.9 GB in this server snapshot) and owned by the server, so it isn't committed. Section 8 shows how to copy it. |
| Server head | `calibration_env/calibration.json` (the current live model), plus six older backups (`calibration_backup*.json` and `calibration_pre_*.json`) |
| Working dataset | Three decoded tables under `data/clean/hardware_models/`: `hardware_model_index` (one row per snapshot), `hardware_model_values` (a change log of every value) and `hardware_model_paths` (the parameter dictionary). They aren't committed - section 8 rebuilds them. |
| Format | Gzipped jsonpickle, with no outer wrapper (section 4) |
| Inner type | `whisqrs.backends.arb_seq.live_arb_seq.LiveArbSeqHardwareModel` |
| Unit | One file is one full device snapshot. |
| Size | About 9 MB decompressed and 0.2 MB gzipped per file |
| Time span | From 2025-01-01. The end date depends on when the server copy was restored (section 3). |
| Cadence | 8 snapshots on a fully active day, fewer on partial days (section 3) |
| Producer | maac `MeasurementEnvironment.save_calibration()` format. The calibration routines write one file at the start of each window, and a separate process writes one after each stage (section 3). |
| Join key | None inside the file. Each snapshot is dated by its server path. |

## 2. Motivation and intended use

The hardware model records the settings the device runs with. After each calibration stage it holds the accepted value of every control parameter, plus the fixed device setup. It's most useful as a dated series of full-device snapshots. Each snapshot gives every qubit's drive and readout frequency, pulse shape, DRAG, amplitude and readout discrimination (`mean_z_map_args` and the confusion matrix), and every coupling's cross-resonance settings and quality at that time.

It's also the source of the `openpulse_features` frames provided in the benchmarking files served by QCaaS (their frequencies match a same-day snapshot exactly) -So if you need the frequency history of each channel, the full record is here. Note that the frames' `phase` is always 0, and the pulse channels' `phase_offset` never changes. The physical channels' `phase_offset` does change, but it's the mixer's I/Q phase correction, not a frame phase. Overall, this data exists so the routines can run the device with the latest accepted settings; use it to find what the device was set to at any time and how each setting moved between calibration runs, for example to measure how much the corrections drift. To see the measurement behind a change, use datasheet 1.

## 3. Provenance and collection

Each snapshot is one `LiveArbSeqHardwareModel` saved with jsonpickle in maac's format. The calibration routines (`oqc-calibration-routines`, through maac) change the live model in memory during each stage. When a stage ends, they save the model to the live file, and a dated copy appears in the archive a few seconds later.

- **A snapshot is the full device state, not a list of changes.** It holds every qubit, resonator, coupling, pulse channel, physical channel, quantum device and baseband (section 4), as they were at save time.
- **Snapshots are dated by their server path** (`.../YYYY/MM/DD/HH-MM-SS_calibration.gz`). The model has no timestamp field of its own. The path time is UTC. It matches the write time stored inside each gzip file.
- **Eight snapshots on a fully active day.** Calibration runs in two windows a day. Window 1 runs `calibrate_1q` and `calibrate_2q`. Window 2 runs `mixer_calibration`, `calibrate_1q`, `calibrate_2q` and `benchmarking`. Each stage leads to one snapshot. One more is saved a few seconds before the first stage of each window (at about 08:45 and 19:05), which makes 8. Days that aren't fully active have fewer, and off-schedule saves add more on some days.
- **Two processes write the archive.** The routine writes the start-of-window snapshot. A different process writes the end-of-stage snapshots (collapsible below).
- **The start-of-window snapshot is usually a repeat.** It holds the model the run loaded, before any stage changes it. In this server snapshot, 98% of these files change no value from the previous file.
- **Only checked values are written, but the checks vary by step.** Most steps test a proposed value against a tolerance or range before writing it, and log a warning when the test fails. Some writes have no check, and some failures reset the value to a safe default instead (collapsible below). A failed step also doesn't mean the parameter kept its old value, because an earlier step may already have written it. For example, Ramsey writes the frequency before the fine-frequency steps run.

<details>
<summary><b>Which process writes each file</b></summary>

- **Start of window: the routine.** Each run builds one `MeasurementEnvironment`, and its `__init__` ends with `save_calibration()`. The routine logs this save ("Saved calibration at …/calibrations/…").
- **End of stage: a separate process.** When a stage ends, the routine calls `update_live_calibration()`, which writes the live file and uploads it. Every stage except `mixer_calibration` saves the head `calibration.json` first. The routine logs no dated save. A dated file appears 7-50 s later (median 9 s). The memory addresses that jsonpickle stores as object ids differ between these files and the routine's own files, so each one came from another Python process. The routine logs don't say which one.
- **Counts in this server snapshot.** Matched against the runner logs, 1,161 files are start-of-window saves and 3,325 follow a live-file update. Only 2 of 3,327 live-file updates have no dated file within 60 s. The other 117 files fit neither pattern. They're off-schedule saves, mostly in February-March 2025 and March 2026.
- **Match files to windows by order, not clock time.** Up to 2025-03-20 the whole morning window ran 45 minutes earlier, starting at 08:00.

</details>

<details>
<summary><b>Which writes are checked</b></summary>

- **No check.** The Ramsey frequency, the Rabi drive scale, the ZMap readout values and the mixer DC biases are written as measured.
- **Not written when the check fails.** FineFrequency, FineFreqShift, XPi2Amp and XPi2Drag skip the write when their proposed value is out of range.
- **Reverted.** `calibrate_2q` reverts a CR step when its amplitude gets too high.
- **Reset to a safe value.** When a Rabi result would push the drive past full scale, the X/2 amp is set to `0.5 / abs(scale)`. A mixer imbalance outside 0.7-1.3 is reset to 1 with a zero phase offset, and no warning is logged.
- **Reset at the start of each run.** Each `calibrate_1q` run first checks the stored values. An amp more than 10% away from its predicted value is reset to the prediction, and a DRAG larger than 9e-9 in size is set to zero.

In this server snapshot, almost every frequency row whose final fine-frequency step failed still changed. Section 7 explains how to count rejections.

</details>


### Where it is on the server

The dated history and the live head are on the LX2 server (datasheet 1 explains how to access it):

```
/data/calibration_restore/oqc_calibration_routines_data/calibration_env/
├── calibration.json                 # current live hardware model
├── calibration_backup*.json         # older backups
├── calibration_pre_*.json           # older backups
└── calibrations/                    # dated daily snapshots (this source)
    └── YYYY/MM/DD/HH-MM-SS_calibration.gz    # date == the path
```

- **The server holds a restored copy, not the live system.** The path root (`…/calibration_restore/…`) is a restore of the production data taken at one point in time, so the archive stops at that date. The live QPU keeps calibrating, so a newer restore ends later and has more snapshots. The copy used for this sheet ends on 2026-08-18.
- **The head and the dated files have the same format.** The head `calibration.json` is uncompressed and the dated files are gzipped, but the same loader reads both (section 8). Each dated file is named from the time it was saved. The head holds the same model as the latest dated file, so the archive alone covers the full history.
- **The backups aren't part of the dated series.** `calibration_backup*.json` and `calibration_pre_*.json` are one-off copies, named by a date or by the change they came before. Most are from before the archive starts.

### Other copies of the hardware model

The model is also uploaded to an Azure blob store, which you can read with `qupboard_client` or the Azure portal. `qupboard` itself only serves the current live head. The blob and the server archive had identical content for the date-matched pair we compared, but the blob holds less: it starts later and skips two saves a day. Use the server archive for history, and the blob only to get the current model.

<details>
<summary><b>Blob coverage in detail</b></summary>

- The blob keeps versions back to 2025-11-20, so it has less history than the server archive.
- Each stage uploads once, when it ends, through `update_live_calibration()`. The start-of-window saves (at about 08:45 and 19:05) aren't stage ends, so a full day has about 6 blob versions against the server's 8.
- The routine's own blob upload stopped after the morning of 2026-06-25. From then on its logs say the blob settings aren't set (they were removed from its `config.toml`), and it uploads only to `qupboard`. The blob still gets later versions, so something else uploads them after that date.
- Occasional uploads outside the schedule make the exact daily count vary (section 9).

</details>


## 4. Composition and structure

**Format and encoding**

On the server, a snapshot is a gzipped `LiveArbSeqHardwareModel` saved directly with jsonpickle, with no outer wrapper. Three things follow from that:

- **The file is the class plus its fields.** The top level has `py/object` (the full class name), `py/state` (its fields) and the object's own `py/obj_ref_id`. Everything else is nested below.
- **Shared objects are stored once and referenced.** The first time an object appears, it's written in full with a `py/obj_ref_id` tag. Later mentions of the same object are a `py/id` that points to that tag. For example, each pulse channel is written in full once inside `quantum_devices`, often nested under a different device that links to it, and the model's `pulse_channels` collection only points to it. So reading a field often means following these references instead of finding the value in place. Enum values are the exception: they're written in full every time they appear.
- **The file depends on specific Python class versions.** jsonpickle records the concrete classes, so library changes can break decoding. That's why the build decodes and flattens the files once (section 6).

`py/state` has 16 top-level keys: nine collections and seven scalar or config keys. The collection sizes below are from this server snapshot:

```text
collections
  quantum_devices             64   # the live qubits and resonators (32 of each)
  pulse_channels             263
  physical_channels           64
  basebands                    7
  instruments                 36
  qubit_direction_couplings   32
  resonators                  32   # references to the resonators in quantum_devices
  qubits                      32   # out-of-date copies (note below)
  sequential_qubit_map        32   # the same out-of-date copies

scalar/config
  error_mitigation, control_hardware, default_acquire_mode, default_repeat_count,
  default_repetition_period, repeat_limit, instruction_limit
```

> [!NOTE]
> **The `qubits` key isn't the live qubit list.** It holds 32 older `qat.purr.compiler.devices.Qubit` objects, and `sequential_qubit_map` points to the same objects. Their own fields, such as `pulse_hw_x_pi_2` and `mean_z_map_args`, never change. Their pulse channels, physical channel and resonator are the live objects, shared with `quantum_devices`. The model's `qubits` property reads from `quantum_devices` and ignores this key. So read qubits from `quantum_devices` (section 6).

**Two layers**

Most of the file is a fixed device description: topology, channel and instrument wiring, the LO/IF frequency plan, DAC limits and pulse-shape design (collapsible below). The daily routines (`calibrate_1q`, `calibrate_2q`, `mixer_calibration` and `benchmarking`) don't change it. They only write a small set of per-qubit and per-coupling operating points:

| Group | Written by | Fields |
|---|---|---|
| 1q drive | `calibrate_1q` (Rabi, Ramsey, FineFreq and XPi2 steps) | drive-channel `frequency` (the CR and cancellation channel frequencies follow it), `scale` (complex or float, from Rabi), `pulse_hw_x_pi_2.amp` and `.drag`, the `freq_shift` channel `amp` (FineFreqShift) |
| 2q | `calibrate_2q` (CR calibration) | CR `scale`, cancellation `scale`, the control qubit's `pulse_hw_zx_pi_4[target].amp`, the target qubit's `phase_comp_target_zx_pi_4[control]` |
| Readout discrimination | ZMap, run daily inside `calibrate_1q` and `benchmarking` | qubit `mean_z_map_args` (both stages), `error_mitigation.readout_mitigation` (`calibrate_1q` only) |
| Mixer | `mixer_calibration` | `physical_channels[*].imbalance` and `.phase_offset`, `dcbiaschannel_pair[I/Q]._bias_value` |
| Coupling quality | `benchmarking` | `qubit_direction_couplings[*].quality`, set to `int(fCX*100)`, or to 50 when the RB fit's R² is 0.8 or lower |
| Readout pulse | `calibrate_readout` and one-off measurements (not daily) | measure and acquire channel `frequency`, `pulse_measure.amp` and `.width`, `measure_acquire.width`, `measure_acquire.delay` (written by `AcquireTimeOfFlight`, which no routine runs) |

The `second_state` channel (the drive for the 1-2 transition) had calibration steps only in 2024. In this archive its `scale` and `frequency` change only outside tracked runs.

The fixed description rarely changes, and almost never at a routine stage end. In this server snapshot, the only stage-end change is on 2026-02-25: at the end of `mixer_calibration`, maac rounded the readout pulse and acquire durations up to a multiple of 8 ns. Other changes to fixed values appear in start-of-window or off-schedule files.

<details>
<summary><b>The fixed device description</b></summary>

| Aspect | What it is | Example fields |
|---|---|---|
| Topology | which qubits exist and how they're coupled | `qubit_direction_couplings` (each has a `direction`, the (control, target) pair such as `(27,34)`, and an `x12` flag), qubit `coupled_qubits`, `sequential_qubit_map`, qubit `id` and `index` |
| Channel and instrument wiring | the analog signal path | `pulse_channels`, `physical_channels`, `basebands`, `instruments`, `control_hardware`, `channel_type`, `hardware_id`, `switch_ch`, `channel_idx` |
| LO/IF plan | the frequencies used to convert signals up and down | baseband `_frequency` (LO about 4.55 GHz for the drive basebands and 9.7-10.4 GHz for the readout ones), `if_frequency` (250 MHz), `_power` |
| DAC and hardware limits | fixed instrument specs | `sample_time` (0.5 ns, so 2 GS/s, on drive channels and 1 ns on readout), `default_amplitude` (16383), `pulse_channel_max_frequency` and `pulse_channel_min_frequency` (3-10 GHz drive, 8-12 GHz readout), DC `max_value` and `min_value` (±32767), `repeat_limit`, `instruction_limit`, `default_repeat_count` |
| Pulse-shape and timing design | gate envelope definitions | `pulse_hw_x_pi_2` `shape`, `width`, `rise` and `phase`; `pulse_measure` `shape` and `rise`; `measure_acquire` `delay`, `sync` and `width`; qubit `discriminator` (always `[0.0]`, never written) |

</details>

**Where each parameter sits** (after following references)

- **Qubit** (`quantum_devices["Q<n>"]`). The pulse definitions are dicts on the qubit: `pulse_hw_x_pi_2` (`shape`, `width`, `rise`, `amp`, `drag`, `phase`), `pulse_measure` (`shape`, `width`, `amp`, `amp_setup`, `rise`) and `measure_acquire` (`delay`, `sync`, `width`). The readout discrimination is also here: `discriminator` (a list, always `[0.0]`) and `mean_z_map_args` (a list of complex numbers). `phase_comp_x_pi_2` is always null. The qubit links to its pulse channels, its physical channel and its resonator (`measure_device`).
- **Pulse channel** (`pulse_channels["Ch<k>.Q<n>.<role>"]`). The values sit on the channel's inner `pulse_channel` object: `frequency`, `scale`, `bias` and `fixed_if`, plus `phase_offset` on about half the channels from 2025-04-11. The `freq_shift` channels (only some qubits have one) also have `amp` and `active`. The roles are `drive`, `second_state` and `freq_shift`, one `cross_resonance` and one `cross_resonance_cancellation` per neighbour, and `measure` and `acquire` for the resonators. Cross-resonance keys also name the neighbour (`Ch1.Q1.Q2.cross_resonance`), and resonator keys use `R<n>` (`Ch2.R1.measure`).
- **Resonator.** Only wiring: the measure and acquire `pulse_channels`, `physical_channel` and `measure_device`. Its measure and acquire channels hold the readout frequency. It has no readout statistics. (`std_dev` is a `pulse_hw_zx_pi_4` shape parameter, not a resonator field.)
- **Physical channel.** The mixer settings `imbalance` and `phase_offset`, the DC biases `dcbiaschannel_pair[I/Q]._bias_value`, and the fixed hardware specs.
- **Coupling.** The coupling object holds only `direction`, `quality`, `x12` and `is_calibrated`. Its cross-resonance settings sit on both qubits: `pulse_hw_zx_pi_4[target]` on the control qubit and `phase_comp_target_zx_pi_4[control]` on the target qubit. The CR channel belongs to the control qubit and the cancellation channel to the target. The control qubit's `pulse_hw_zx_pi_4` also has entries for pairs that aren't couplings, and those never change.
- **Readout error mitigation.** `error_mitigation.readout_mitigation.linear` holds a 2×2 confusion matrix per qubit, keyed by the qubit number as a string (`"1"`, `"15"`).

> [!NOTE]
> **Values have mixed types.** Most are floats, but `bias` is complex and `scale` is complex or float depending on the channel. Some fields are enums (e.g. baseband `_reference`), a few are lists (`discriminator`, `mean_z_map_args`), and the confusion matrix is a nested dict. Don't assume a value is a float. The build keeps each of these types (section 6).

## 5. Distribution and statistics

The numbers in this section come from the server snapshot used for this sheet (2025-01-01 to 2026-08-18). A newer copy will give different numbers, but the patterns should hold.

- **Most active days have exactly 8 snapshots** (85% of active days in this server snapshot), which matches the schedule in section 3. A few days have fewer, usually because only part of the schedule ran. A few have more, up to 36 in one day. Those days have off-schedule saves, an extra routine run, or both (section 3). Some calendar days have no snapshot at all, so the average per calendar day is a little under 8.
- **Coverage is steady.** Monthly counts are close to 240, with dips in months that had idle stretches (March 2025 and March 2026 in this server snapshot) and in the partial final month.
- **Every snapshot is unique.** No two files share a sha256 hash, so there are no duplicates to remove.
- **File sizes are tightly clustered.** Gzipped files are about 200 KB each and decompressed files about 9 MB, growing slowly as the device setup grows. No file is missing parts of the model. The only gaps are dates with no file.

## 6. Preprocessing and cleaning

The build decodes each snapshot and writes **three Parquet tables** to `data/clean/hardware_models/`. Analysis code reads only these tables, so it never needs jsonpickle or the model classes. Section 8 has the commands.

<details>
<summary><b>Why decode here, when datasheet 1 leaves its payloads undecoded?</b></summary>

The two sources sit at opposite ends of the same trade-off. The hardware models all use one class with one structure, so a single walker can flatten every snapshot without losing values. Each snapshot is also mostly the same as the one before, so the change log is small: in this server snapshot about 40 GB of decompressed JSON becomes an 8 MB table. Datasheet 1's payloads are the opposite. They use dozens of schemas, so no single flattener works. They're hundreds of GB of mostly unique raw sweep data that wouldn't shrink as a change log (datasheet 1, section 6). And most analyses only need one `measurement_type`. So those payloads are downloaded and decoded when needed.

</details>

### The three tables

| Table | One row per | Key columns |
|---|---|---|
| `hardware_model_index` | snapshot | `sha256` (key), `timestamp`, `qpu_id`, `server_path`, `size_bytes`, `loads_ok`, `model_class` |
| `hardware_model_values` | stretch of time over which one `path` held one value | `path`, `path_template` (joins to `paths`), `dtype`, one filled `value_*` column, `valid_from_ts` and `valid_to_ts` (with matching `valid_from_sha` and `valid_to_sha`), `n_snapshots` |
| `hardware_model_paths` | parameter (a path with its ids removed) | `path_template` (key), `entity`, `dtype`, `enum_type`, `n_changes`, `n_distinct`, `writer_stage`, `writer_measurement` |

`values` is the main table, and the other two describe its rows: `paths` says **what** each parameter is, and `index` says **when** each snapshot was saved. `values` joins to `paths` on `path_template`, which is the `path` with its ids removed. Each row's `valid_from_sha` and `valid_to_sha` point to the exact files in `index`. You need all three tables to rebuild an original snapshot.

**`values` is a change log.** It only adds a row when a value changes, so each row covers a stretch of time over which one `path` held one value. Most values stay the same between snapshots, so this is much smaller than one row per parameter per snapshot. In this server snapshot it keeps under 2% of the cells (about 690k rows instead of 39 million). It also makes time-series questions simple, such as "how did this value change?" or "what was it at time t?". Reading the whole device at one time takes more work, so the package has a helper for that (section 8).

**What the build keeps and drops.** It keeps every leaf value and the links between shared objects, so no value is lost. Collections are keyed by a stable id (a device id such as `Q12`, a coupling `direction`, or a channel key) instead of list position. A shared object is stored once, and other places point to its path. The build drops three kinds of detail:

- jsonpickle's internal ids (`py/id`) and the order of sets.
- The exact Python container and number types. Tuples and numpy arrays become JSON lists, numpy scalars become plain numbers, and dict keys become strings.
- Object fields hidden behind a class property with the same name, which normal attribute access never sees. These are the model's `qubits` list (out of date), its `resonators` list (the same objects as in `quantum_devices`) and the `driver` field on each instrument and on `control_hardware`. `driver` is always null, because qat drops the hardware connection when it saves. The build keeps the `_driver` field that the property reads, which is also null.

> [!NOTE]
> The collapsibles below cover the details: how values are encoded, how templates and the writer columns in `paths` work, and where the out-of-date qubit copies come from. You'll only need them if you read `values` directly instead of through the helpers in section 8.

<details>
<summary><b>Value encoding: <code>value_*</code> columns, null, empty and absent, comparisons</b></summary>

- **One value column per row.** Exactly one `value_*` column is filled, matching the row's `dtype`. The exception is `null`, which fills none, because the `dtype` already says what the value is. Most values are plain `int`, `float`, `bool` or `str`. Complex numbers become an `[re, im]` JSON pair. Enums become the member name, and `paths` keeps the enum's class so the type isn't lost. Refs become the path they point to.
- **Lists stay whole.** List values such as `discriminator` and `mean_z_map_args` are kept as one JSON value, so one parameter is always one row. The per-qubit confusion matrix is the exception. It's a nested dict, not a list, so each cell becomes its own float value (e.g. `…readout_mitigation.linear{13}{0|1}`).
- **Null, empty and absent are different.** A row ends when the value changes or the path disappears. A stored `null` and an `empty` list or dict both have rows. A path that doesn't exist at that time has no row at all.
- **`valid_to_ts` is the last snapshot that still held the value.** It isn't the time the value was replaced; that's the next row's `valid_from_ts`. So `valid_to_ts - valid_from_ts` is one snapshot gap shorter than the time the value was in use, and a row that covers one snapshot has zero length. It's NaT if the value is still current.
- **`valid_from_ts` is when the first file with the value was saved, not when the value changed.** A routine's change is saved after its stage ends (section 3), and a change made between runs shows up in the next file. So `valid_from_ts` is only an upper bound.
- **One `path` is always the same parameter.** A path never disappears and comes back, and each `ref` keeps its target. New paths come from new fields and coupling changes.
- **A snapshot that fails to decode ends every row.** It stays in `index` with `loads_ok = False`, and the helpers in section 8 raise an error for it.
- **Comparisons are exact.** The build compares values bit for bit (with `float.hex`). So `+0.0` and `-0.0` count as a change, and so does a type change, such as a mixer `phase_offset` reset to the int `0` after a float. All NaNs compare equal, so a stretch of NaN values stays one row. Exact comparison also means rounding-level changes start a new row. In this server snapshot about 600 rows differ from the previous value by less than 1e-9 relative, mostly mixer `phase_offset` values and channel frequencies that move by a few Hz. Use a tolerance when you count real updates.

</details>

<details>
<summary><b>The <code>paths</code> columns: one template per parameter, and its writer</b></summary>

The `paths` columns fall into three groups:

- **What the parameter is:** `path_template`, `entity`, `dtype` and `enum_type`.
- **How it behaves in the archive:** `n_changes`, the number of value changes summed over the template's paths, and `n_distinct`, the number of different values across them. Use `n_changes > 0` to find parameters that change. `n_distinct` is above 1 whenever qubits differ, even if nothing changes.
- **What writes it:** `writer_stage` and `writer_measurement`, two lists in matching order that hold the daily routine stage and step of each writer. Both are empty if no daily routine writes the parameter.

**One template is one parameter.** The template replaces each number inside a `{…}` key with `*`, except the confusion-matrix cell labels (`0|1`), which are field names. The device kind, channel role and field name stay, for example `quantum_devices{Q*}.pulse_hw_x_pi_2{amp}`, `pulse_channels{Ch*.Q*.drive}.pulse_channel.frequency` and `qubit_direction_couplings{direction=(*, *)}`. `dtype` is `mixed` where a parameter changes type over time.

The writer columns come from a hand-made map read from `oqc-calibration-routines`, and match the table in section 4. A parameter with several writers lists them all. For example, `mean_z_map_args` has `[calibrate_1q, benchmarking]` and `[zmap, zmap]`, and the drive frequency has `calibrate_1q` twice, once for `ramsey` and once for `fine_frequency`. An empty list doesn't mean the value is fixed: the readout pulse and frequencies, `second_state`, and some pulse-shape and LO settings have no daily writer but still change outside the daily stage ends (section 4). To find which run wrote a given change, match the file's time to the runner logs (section 3).

</details>

<details>
<summary><b>The out-of-date qubit copies (<code>sequential_qubit_map</code>)</b></summary>

Every file holds a second set of 32 qubit objects. They're the model's own `qubits` list, which the class hides behind a `qubits` property that rebuilds the list from `quantum_devices`. The build skips fields hidden by a property, so there's no `model.qubits` or `model.resonators` path. But `sequential_qubit_map` is a plain field that still points to the old list, so the copies appear under `model.sequential_qubit_map{0..31}` (`entity == "sequential_qubit_map"`). Nothing in the installed qat, whisqrs or maac code reads that field. Each copy holds one value for the whole archive, and it doesn't match the live one. For example, Q1's X/2 amp is 13.61e6 there and 6.87e6 in the 2026-08-18 model.

</details>


## 7. Limitations and caveats

- **Raw files need the model classes.** You have to follow references to read any value, and jsonpickle ties the format to specific class versions. Use the clean tables unless you need the live object (section 8).
- **Rebuilding is slow.** Each snapshot takes about 0.3 s to decode and flatten, so a full rebuild takes about 20 minutes in this server snapshot. Keep the tables instead of decoding again.
- **No units.** No parameter records its unit, so you have to infer it from how qat uses each field.
- **The dated archive starts 2025-01-01.** Before that there are only five named backups (2024-09-25 to 2024-11-22), not a daily series.
- **Only accepted values are recorded.** Comparing consecutive snapshots (what the change log does) gives the full history of accepted changes. To see the fit behind a change (model, R², parameters) and its old and new values, you need the raw measurements in datasheet 1. A proposal that a check rejected never appears here (section 3).
- **A warning doesn't mean the parameter was rejected** (section 3). A rejection means the run tried the entity at a step that writes the parameter (or warned about it), and the new value is missing or unchanged.
- **Rounding-level changes count as changes.** Use a tolerance when you count updates (section 6).
- **The copies under `sequential_qubit_map` are out of date.** Read qubits from `quantum_devices` (section 6).

## 8. Access and usage

The package has loaders and two query helpers for the three tables. Build the tables once, then load and query.

### Copy the archive

The build reads the archive from a local folder. Copy it once with rsync and keep the copy, because the build decodes these `.gz` files directly. It's about 0.9 GB in this server snapshot, and a rerun only copies new files.

```bash
rsync -a example@oqc.tech@10.20.41.37:/data/calibration_restore/oqc_calibration_routines_data/calibration_env/calibrations/ \
    data/raw/hardware_models/
```

### Build the tables

Decoding needs the model classes from `maac`, which requires `numpy<2`. The analysis environment uses `numpy>=2`, so the build runs in a separate `.venv-maac`:

```bash
uv venv --python 3.12 .venv-maac
uv pip install --python .venv-maac/bin/python \
    --extra-index-url https://pypi.int.oqc.app/simple/ \
    maac==1.5.8 jsonpickle==4.1.2  # needs VPN, no credentials required

# PYTHONPATH=src makes the package importable without installing it
# (installing it would bring numpy>=2 back)
PYTHONPATH=src .venv-maac/bin/python -m qpu_drift.sources.hardware_models.build \
    --source data/raw/hardware_models
```

The versions are pinned because `oqc-calibration-routines` pins maac 1.5.8, and these versions decode every file in this snapshot. Don't run the `build-hardware-models` command: it's installed in the analysis environment, which has no maac, so it stops with a message pointing to `.venv-maac`. A full build is slow (section 7), so rebuild only after you copy new snapshots.

### Load the tables

```python
from qpu_drift.sources.hardware_models import load_index, load_values, load_paths

idx    = load_index()
values = load_values()  # or keep a subset: load_values(entity="quantum_devices"),
                        # load_values(path_template="model.quantum_devices{Q*}.pulse_hw_x_pi_2{amp}")
paths  = load_paths()
```

### Query one value over time, or the whole device at one time

The two helpers cover the two ways to read the change log:

```python
from qpu_drift.sources.hardware_models import value_as_of, snapshot_state

# (a) over time: one path's value in the latest snapshot at or before a time
v = value_as_of(values, "model.quantum_devices{Q1}.pulse_hw_x_pi_2{amp}", "2026-08-01")

# (b) at one time: every path in one snapshot, as a DataFrame
state = snapshot_state(values, sha="…")                 # the snapshot in that file
state = snapshot_state(values, timestamp="2026-08-01")  # the snapshot in use at that time
```

Both helpers read the latest snapshot at or before the given time. They use the index, which they load from disk unless you pass `index=`. So a path the model had dropped by then, such as a reversed coupling, comes back as absent, not as its last value. `value_as_of` returns `None` only for a stored `null`. It raises `KeyError` when the path is absent or the time is before the archive (`snapshot_state` returns an empty frame instead), and `ValueError` when that snapshot failed to decode.

> [!NOTE]
> **Three things to know before you analyse.**
>
> 1. **Identity is in the `entity_id` string.** The `entity_id` is the object's key in the model (section 4), such as `Q<n>`, `Ch<k>.Q<n>.drive` or `direction=(<control>, <target>)`. There's no `qubit_index`, `control` or `target` column. `groupby("entity_id")` works as it is. Parse the ids with a regex in your derived table only when you need them as numbers.
> 2. **`dtype` is set per row.** Some parameters change type over time: the drive `scale` is complex in some periods and float in others, and a mixer `phase_offset` reset stores the int `0`. Read values with `decode_value` or through each row's `dtype`, not by casting a column to `float`.
> 3. **Refs describe wiring, not values.** A `ref` row (such as a pulse channel's `physical_channel` or a qubit's `coupled_qubits`) links to another object, so parameter analysis can ignore it. Its `value_str` names an object, not a path in `values`, so follow it with a prefix match (`path.str.startswith(target)`).

<details>
<summary>If you need the live <code>LiveArbSeqHardwareModel</code> object</summary>

The clean tables cover most analysis. Only use the live model object when you need to *run* it or read something the tables don't include. The wrapper below only imports maac when you call it, so run it in `.venv-maac`:

```python
from qpu_drift.sources.hardware_models import decode_snapshot
hw = decode_snapshot("data/raw/hardware_models/2026/08/18/09-13-31_calibration.gz")
```

It wraps maac's `load_calibration`, which is the read counterpart of the routine that wrote the archive. If you'd rather not use the wrapper, these three lower-level calls give the same object:

```python
# 1. maac - reads .gz or .json directly (the library that wrote the files; recommended)
from maac.measurement_environment.encoder import load_calibration
hw = load_calibration(path)

# 2. qat FileModelLoader, from an uncompressed .json path (gunzip the .gz first)
from qat.model.loaders.purr.file import FileModelLoader
hw = FileModelLoader(path).load()

# 3. qat Calibratable, from a jsonpickle string (picks the right subclass)
from qat.purr.compiler.devices import Calibratable
hw = Calibratable.load_calibration(cal_string)
```

Without these libraries, you can follow the `py/id` and `py/obj_ref_id` references yourself.

</details>

## 9. Open questions
- **Blob coverage.** We don't know what uploads the blob versions after 2026-06-25, or the extra off-schedule ones (section 3).
- **jsonpickle versions.** The runner logs record only the `qat-compiler` version (2.0.3 up to 2.7.0), and only until 2026-05-25. They don't record maac, whisqrs or jsonpickle, or anything for the end-of-stage writer.