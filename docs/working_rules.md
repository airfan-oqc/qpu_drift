# Working rules

These rules apply to every analysis of this data. Each rule ends with a tag that names the section with its evidence. Tags A1 to C3 point to [Phase 01](phases/01_eda.md), and tags D1a to D3c point to [Phase 02](phases/02_hypotheses.md). The [section key](#section-key) links each tag. The terms signal ratio, blocks and censoring are defined in the [Phase 01 approach](phases/01_eda.md#approach).

Each rule appears once, in its current form. When a new result changes a rule, we rewrite it here and add the new section to its tag.

## Contents

- [Working rules](#working-rules)
  - [Contents](#contents)
  - [Checklist](#checklist)
  - [General rules](#general-rules)
    - [Load and join](#load-and-join)
    - [Periods and breaks](#periods-and-breaks)
    - [Exclusions](#exclusions)
    - [Rejections and censoring](#rejections-and-censoring)
    - [Noise and signal](#noise-and-signal)
    - [Model and test](#model-and-test)
  - [Rules by data](#rules-by-data)
    - [DS4 fridge](#ds4-fridge)
    - [DS2 hardware models](#ds2-hardware-models)
    - [DS1 measurements](#ds1-measurements)
    - [DS3 KPIs](#ds3-kpis)
    - [Applied 1q parameters](#applied-1q-parameters)
    - [f01](#f01)
    - [Ramsey T2\*](#ramsey-t2)
    - [calibrate\_1q warnings and DRAG zero](#calibrate_1q-warnings-and-drag-zero)
  - [Section key](#section-key)

## Checklist

Every analysis does these. Each line links to the full rules.

1. Treat timestamps as UTC, and link DS1 rows to runs by `result_id` ([Load and join](#load-and-join)).
2. Pick the study period from the sources in use, and import every date from `periods.py` ([Periods and breaks](#periods-and-breaks)).
3. Split at `BREAKS` and the thermal cycle, and never interpolate across a gap ([Periods and breaks](#periods-and-breaks)).
4. Leave out `INCIDENTS`, the `F01_FLAGS` windows, unmeasured qubits and runs with no `result_ids` ([Exclusions](#exclusions)).
5. Count a rejection only where the run attempted the entity, and check censoring per entity ([Rejections and censoring](#rejections-and-censoring)).
6. Estimate the noise first, and only analyse a variable at lags where its signal ratio reaches 2 ([Noise and signal](#noise-and-signal)).
7. Take sample sizes from blocks, not from row counts or AR(1) ([Model and test](#model-and-test)).
8. Test drift on one chip-wide series per metric and stratum ([Model and test](#model-and-test)).
9. Check change points against the register, and don't read one series' change points as device events ([Model and test](#model-and-test)).
10. Read the [rules for each source and quantity](#rules-by-data) you use.

## General rules

### Load and join

- **UTC.** Treat every timestamp with no timezone as UTC. Join DS4 on time with no offset (A1).
- **DS1 to runs.** Link DS1 rows to runs by `result_id`, not by time. Only link `valid` ids: ids from before the DS1 restore (2024-09-18) point at the wrong rows (A3, B3a).
- **DS4 to runs.** Join MIX as a daily robust median, not the nearest 6 h reading. Epochs 1 and 3 appear to have a daily cycle, which a 6 h join brings in and a daily median doesn't. Other DS4 channels can use `merge_asof(..., tolerance=pd.Timedelta("6h"))`. Never use a reading across `DS4_GAPS` or `THERMAL_CYCLES` (B1, C3).
- **Parsing IDs.** Use `qpu_drift.ids`. No mapping table is needed (A2).
- **Directed couplings.** `1-10` and `10-1` are the same edge but separate calibrations. Some couplings reverse partway through: C10-T1 and C2-T1 are C1-T10 and C1-T2 reversed (A2, A4, B3a).
- **Applied values.** Take them from DS2 (`snap_after`). `output_info` holds the proposal. Take DC bias from `output_info` after 2026-05-25 22:01, when DS2 stops recording it (A3, B2).
- **Applied f01.** Take it from `output_info` or DS2, not from Ramsey `new` (A3).
- **Applied 1q values from payloads.** Take the applied value from the run map, and match payload targets on (`result_id`, `key`), not the payload id alone. A payload's final step is the applied value only when it equals the run-map `new` (B3c).

### Periods and breaks

- **Study period.** Pick it from the sources in use (the [A4 table](phases/01_eda.md#a4-study-periods-gaps-and-breaks)). Import dates from [periods.py](../src/qpu_drift/periods.py) instead of retyping them, and filter with `within()` (A4).
- **Breaks.** Split at `BREAKS` with `segment()`, including the `ds4` break at the end of the thermal cycle. For `calibrate_1q` rows, add the code regime (A4, B1).
- **Process events.** Treat the 2025-03 outage and the thermal cycle as steps in every KPI and 1q model (D1b sections 4 and 5).
- **Gaps.** Never interpolate across a gap or an outage. Each outage ends in a break: the 2025 ones in `BREAKS` directly, the 2026 one through the thermal cycle and the coupling flip (A4, B1).
- **Settling.** Keep qubit data inside `SETTLING` as its own stratum, or leave it out. Never pool it with settled data (B1).
- **Thermal cycle.** Stratify step spreads at 2026-03-24 and don't pool across it (B2).
- **Gating eras.** DS3-only work splits at `gating_regime` (A4).
- **Coupling sets.** Stratify 2q series by coupling set (B3a section 5 lists who leaves and joins at each change). Drop a coupling's no-trace rows after its last `cr_u` row from 2q failure counts. Drop `coupling_retired` rows from fCX rates (B3a, B3d).

### Exclusions

- **Excluded rows.** Before any noise or drift estimate, leave out every row of every source and parameter inside `INCIDENTS`, the `F01_FLAGS` windows (`f01_flagged(times, qubits)`), qubits that a reduced run didn't measure (the `measured` flag), and runs with no `result_ids` (A2, A3, B1).
- **Silent failures.** Treat runs with no `result_ids` as missing runs, not "no change" (A3).
- **Q8.** Drop the 2024-03-21 `calibrate_1q` file and the two static Q8 entries in DS2 (`5-8` and `15-8`) (A2).
- **Q23.** Treat its f01 before and after its excursion as two levels. A median across both is about 15 MHz off for the earlier level (A2).
- **Untracked DS2 writes.** Drop any DS2 or run difference that spans a write in `UNTRACKED_DS2` (`spans()`). Drop DS2-only differences that span an `UNTRACKED_DS2_OTHER` snapshot. Also drop any difference that spans an incident start (`spans(t0, t1, [i.period[0] for i in INCIDENTS])`): the first run after the 2026-03-24 rollback starts from the restored 2026-03-05 values. For the smaller untracked snapshots, compare each run's `before` with the previous run's `after` (A4, B1, B2).
- **Missing register entries.** Add the six unregistered no-run snapshots of 2026-03-24 to 03-30 (D1b section 6) and the 2025-02-12 11:18:02 drive-scale write (D1b section 9) to `UNTRACKED_DS2`.

### Rejections and censoring

- **Rejections.** A warning isn't a rejection: it names one failed ladder step, and the earlier steps still apply. Neither is a missing or unchanged value on its own. Count a rejection only where the run attempted the entity (the `measured` flag or a warning for it) at a step that writes the param (`STEPS_1Q`), and the new value is missing or unchanged. Censoring uses the same denominator. A `rabi` id isn't an amp attempt, and a failed final ladder step isn't a rejection (A3, B2).
- **Not rejections.** Take rejections from B2, not from unlinked DS1 rows or warnings. Earlier-`cr_u_N` 2q rows aren't failures (B3a).
- **Missing steps.** Open 1q rows (mostly the `freq_shift` qubits Q2, Q13, and Q23 until 2025-02-12), and runs where a fixed qubit group loses a step, are missing data, not attempts. Keep them out of the reject count and the step series (B2).
- **Censoring per entity.** Check it per entity, not pooled: a pooled share can pass while one entity is always rejected. Treat a chronically rejected series as stale for that stretch (B2).

### Noise and signal

- **Upper bounds.** The spread of lag-1 differences bounds any sample-independent scatter from above. It isn't a noise estimate, because it also contains real change within one sampling interval (B1 section 4).
- **Two estimates.** Where a variable has an independent noise estimate (fit error, repeats within a run), write both and say in `method` which is which. Record which estimate a pass rests on. A pass against the smaller estimate alone is weaker evidence (B, C).
- **Noise rows.** Write to `data/derived/noise/noise_floor.parquet`: one row per source, param, entity, regime and method, with columns `source`, `param`, `entity`, `regime`, `start`, `end`, `method`, `sigma`, `unit` and `n`. Compare estimators that should agree in the notebook that writes them (B).
- **Resolution.** Analyse a variable only at lags where its signal ratio reaches 2, and never at a finer resolution (C).
- **Lag-1 signal isn't drift.** $L^*$ flags extra run-to-run scatter (C1).

### Model and test

- **No AR(1) sample sizes.** Don't use AR(1) effective sample sizes. MIX doesn't follow AR(1), and only fRB is close to it at lag 2 (B1, B4).
- **Blocks.** Take the sample size of slowly varying series from independent blocks, and their uncertainty from a block bootstrap, not from the row count. Take timescales and block lengths from series that reach every lag. B4's per-lag medians are over whichever groups reach each lag (0.52 to 0.71 of them at 28 runs) (B4, C1).
- **Chip-wide series.** The KPIs and amp × scale share a chip-wide common mode. Test drift on one chip-wide series per metric and stratum, with runs as the sample. Don't bootstrap over elements (C1, C2).
- **Levels, not trends.** Treat DS3 KPIs within a stratum, and amp × scale between its chip-wide change points, as levels (C1, C2).
- **Breaks need a direct test.** The change-point screen can't see `BREAKS` or `THERMAL_CYCLES`. Test those by comparing adjacent strata (B4 section 5).
- **Register matches.** Check candidate change points against `BREAKS`, `OUTAGES`, `DS4_GAPS`, `UNTRACKED_DS2`, `F01_FLAGS`, `INCIDENTS` and `THERMAL_CYCLES`. A change point that matches none of them is a lead to record (B).
- **Per-series change points.** They match the register no better than chance, for the KPIs and the 1q params alike. Don't read them one at a time as device events (C1, C2).

## Rules by data

### DS4 fridge

- **Event marker.** Use the fridge as an event marker: `THERMAL_CYCLES`, `SETTLING` and the epoch boundaries. 2026-08-19 is a date to compare qubit series against, not a DS4 event (B1, C3).
- **Epochs.** Work within cold epochs (`cold_epochs`) and drop each epoch's settling. Leave out epoch 2: from 2026-07-02 to 07-07 MIX is an event marker only (B1, C3).
- **Centring.** MIX steps down between epochs. Centre it within each epoch and add a time term, or make epoch a fixed effect, before relating it to anything. Per-epoch slopes describe each epoch; they aren't trends (B1, C3).
- **Lags.** Only test MIX changes over 2 days or more (B1).
- **Gap boundaries.** They are missing data, not fridge events. DS4 has nothing from 2026-06-11 to 07-02, so it says nothing about the fridge before the July incident (B1).
- **Sample size.** Count 28-day spans, not rows or AR(1) $n_\text{eff}$. Bootstrap MIX with blocks of 28 days in epochs 0 and 1, and 14 days in epoch 3. 14 days is a close call, but epoch 3's results hold with 28 too (B1, C3).
- **Other channels.** Summarise the fridge with eight channels: the five stages plus `outlet_water_temperature_...02`, `flow_3CL_FM_01` and `oil_temperature_...01`. PT1, PT2 and flow are mostly trend, so detrend them within each epoch (or difference at 2 days or more) and use their `detrended_*` noise rows. MIX isn't, so use its rows with the trend in (B1).
- **Noise rows.** Read the `lag1_robust_over_sqrt2` rows as an upper bound on sample-independent scatter, not as instrument noise (B1).

### DS2 hardware models

- **Writers.** `paths.writer_stage` lists every daily stage that writes each parameter (one template per parameter), and `writer_measurement` the step of each. Take the writer of a single change from the run map (B2).
- **`cr_scale`.** Profile it as a phase plus a separately set modulus, not as real and imaginary parts: `calibrate_2q` only ever rotates it (B2).
- **1q amp.** Remove each run's chip median before reading amp steps as local. The `1q.amp` noise rows still include the offset (B2).
- **Noise rows.** The `ds2` rows (1q and 2q) are a lag-1 upper bound on step noise, given acceptance. Mixer has none, so its noise needs another estimator. Mixer values are on a search grid (B2).
- **DS2-only data.** CR/CRC `pulse_channel.frequency` and `phase_comp_target_zx_pi_4` change but are only in DS2, so profile them from there if needed. Read drive `scale` as its real part: the imaginary part is always 0 (B2).
- **Static layer and topology.** Split at these if a parameter depends on them: the basebands (2025-02-12 13:40:14), the pre/postcomp restructure (2025-03-18 19:06:53), the coupling reversal (2026-03-24, in `BREAKS`), and `phase_offset` appearing on 2025-04-11, which isn't in the register. Readout pulses, `second_state` and pulse shapes also change outside the daily runs, mostly in 2025-02 to 03 and 2026-03 (B2).

### DS1 measurements

- **Fetch lists.** B3b to B3d read their payloads from `data/derived/measurements/fetch_<family>.txt`, all fetched. `fetch_rb2q_rest.txt` is needed only if B4 needs fCX per run, or era-1 fCX comes back in (B3a, B3d, B4).
- **FineFreqShift.** Fetch it with the frequency family, but profile it as the `freq_shift` channel amplitude (B2), apart from f01. Q2 and Q13 have no FineFrequency, Ramsey or XPi2Drag rows. FineFreqShift has no ladder, so it has no scaled σ (B3a, B3c).
- **Linkage per entity.** Report unlinked rows per type and entity, split by `INCIDENTS` (B3a).
- **Routine series.** Keep `outside runs` rows and `cr_u_stability` out (B3a).
- **2026-06.** Stratify durations and RB at 2026-06. B3b finds no RB setting change there, and B4 finds no RB step (B3a, B4).
- **Settings.** Read them per payload from `config_settings.parquet`. RB2q, CRUnitaryIZ, ZMap and the mixer types are weekly samples, so their change dates are only accurate to a week (B3b).
- **1q strata.** Stratify FineFrequency and XPi2Drag by `periods.segment`, and every 1q type by the payload's own `delay` or `N`, not the run's ladder. The one-name XPi2Amp `8` and `4 > 8` runs (2025-07-03 to 08-24) are their own stratum. Use the spine, not the claimed payloads, to see which steps ran. Keep B2's split at 2026-03-24. Don't stratify Ramsey by `time`. Rabi has no noise rows (B3b, B3c).
- **Ramsey sweep.** `time` appears in every run from 2026-05-26 (and 7 runs before), so check the sweep per payload (B3b).
- **RB2q lists.** Stratify fCX by `rb_lengths` (payload, else predicted), but don't read a list contrast as an effect: the list follows the previous fCX. Count payloads per month from `coherence_rb` only, since the ones local before B3a are DS3 rejects (B3b, B3d, B4).
- **CRUnitaryIZ.** `N` = 1 runs are reverted N=2 steps (or incidents), so treat them as a stratum in 2q series (B3b).
- **Mixer and bands.** If mixer results are used, split at `use_switch_hive` (2026-05-25 to 06-01), and treat the 2024-10-16 to 2025-01-27 sideband settings as a stratum (B3b).
- **1q noise rows.** Use the `ds1_1q` rows: `final_step_fit_sd_median_x_rsd_zc` for the low end and `final_step_fit_sd_median` for the high end, matched on param, qubit and stratum. Don't pool final settings (B3c).
- **Reading the ratio.** Read each qubit-stratum by its 95% interval: reaches 2, below 2, or unclear. Below 1 against the centred σ means the centred σ overstates the noise there (B3c).
- **KPI supplement.** B4 reads `kpi_supplemented` (`load_kpi_l2(KPI_SUPPLEMENTED_PATH)`). Its DS3 columns equal `kpi_enriched`, so DS3-only results still apply. Use `error`, else `payload_error`, as `error_source` records, and count rows with neither separately (B3d).
- **Seed error.** Report `seed_error` beside the fit error, on `ok` rows: the `low_r2` fits have its long tail. Treat a NaN as missing, and count stored fits with $x_0 < 0$ separately (B3d).
- **Cache end.** Run payload-based results to the cache end (2026-08-17). After it, rows have no payload and couplings can't be judged retired. `in_incident` marks only the thermal-cycle run (B3d).

### DS3 KPIs

- **Which rows.** Good-value analyses (noise, drift, correlation) use the B4 kept rows: `ok` under the one gate, outside `INCIDENTS`, the f01 flags and retired couplings. Bad-value analyses (censoring, missingness) read `kpi_supplemented`. Count `no_r2` rows (all of era 0 for T1, T2, fCX and fRB) separately, as neither kept nor censored (B4).
- **Per element.** Model KPIs per element and stratum. A chip-wide run term adds little (D1b section 2).
- **Payload-filled T1 and T2.** Treat the filled rows from 2025-04 to 2025-11 as `ok`, since their fit error matches DS3's. Keep `value_source`, so a change at 2025-04-11 can be checked against it (B4).
- **Censoring.** Drop entities censored above 10%, or model the censoring. How `unresolved` rows are read doesn't change which. State the fCX level bias, and don't fill in censored values: they are far from the kept ones (B4).
- **T1 and T2 against the sweep.** Report values above the 80 µs sweep separately. Report T2 sizes with the variant capped at the sweep beside the main one (B4, C1 section 6).
- **T2 fit quality.** r2 doesn't rank T2 fits, so screen or weight T2 by its fit error. Its low signal ratios are on element-strata below 40 µs and above 120 µs, and the log scale doesn't change that (B4).
- **Noise rows.** Take the main estimate from `noise_floor.parquet`: `fit_error_median` for T1 and T2, `seed_error_median` for fRB, `seed_error_median` else `fit_error_median_x_sample_seed_ratio` for fCX, and `binomial_upper_median` for fRO. Never use the RB fit error alone (B4).
- **Start dates.** Start T1, T2 and fRB at 2024-10-15, fCX at 2025-07-07 (era 1 has no fCX noise estimate) and fRO at 2024-05-28. Skip strata 3, 4, 6 and 12 and `SETTLING`; strata 5 and 10 have no DS3 runs (B4 section 6).
- **fRO near the floor.** Leave out the `ok` fRO values within 0.02 of the depolarising floor as failed readouts (B4).
- **fRO change points.** Give fRO a step at the thermal cycle. Find its change points with CUSUM and a block of 14: the jump scan misses the shared steps (C1).
- **fRO across the thermal cycle.** fRO element levels change across a thermal cycle, so don't pool them (D1b section 3).
- **fRB.** Use it per element only on qubit-strata that pass against `seed_error`; otherwise use a level pooled across qubits (B4, C1). Leave out [DRAG-zero episodes](#calibrate_1q-warnings-and-drag-zero).
- **2025-12 and 2026-07.** Use these as dates to compare against, not as events. Nothing outside the KPIs records them (D1b sections 5 to 8).
- **Frames.** DS3 drive frames equal DS2 wherever both exist, so after 2026-08-18 they can replace the drive frequency (B4).
- **Missing runs.** Treat the benchmarking runs with no DS3 run as gaps. They all have `result_ids`, so they can be rebuilt from the payloads if a window needs them (A3, B4).

### Applied 1q parameters

- **Noise.** Read 1q signal against B3c's centred σ, and keep the fit sd as the high end. For freq this matters most: against the fit sd it has signal from lag 1, against the centred σ only from about a week (14 runs). So compare freq at lags of a week or more (C2).
- **Drag and freq lags.** Most qubit-strata are unclear or at noise level at lag 1, so test them at longer lags (B3c).
- **amp × drive scale.** Model amp as amp × drive scale (`1q.amp_x_scale` rows, in ln), not alone (B3c, D3a).
- **Time of day.** amp × scale is lower in the evening. Shift evening rows to the morning level, or add a time-of-day term, before comparing it with anything. freq and drag don't need this (C2).
- **freq steps.** Give freq its own step at the thermal cycle and at the 2025-03-18 break ([f01](#f01)). drag gets no boundary step (C2).
- **drag and `freq_shift` amp.** drag has no common mode and many series never reach signal, so it goes in pooled. `freq_shift` amp has 2 or 3 qubits per stratum, so model it per qubit only (C2).
- **Correction terms.** Frequency offsets and short amplitude ladders need no correction term. But fRB is censored more often after a short ladder, so censored fRB rows aren't missing at random (D3a sections 4 and 5).

### f01

- **Per qubit.** Model f01 drift per qubit with `INCIDENTS` out. A chip-wide factor adds little, and there's no slow chip-wide term and no fridge term on settled stretches (D1a sections 6 and 7).
- **Model.** Model f01 per qubit with jumps and outliers (M3, or M3s with stage noise), not a Gaussian random walk. Refit on an expanding window (D2b sections 3 and 13).
- **Use for the next run only.** Don't use M3 for staleness over weeks. It overstates large moves at 7 and 14 days, and the Allan deviation at 16 days (D2b sections 5 and 7).
- **Intervals per period.** M3 intervals are too wide before the thermal cycle and too narrow after it, so check them per period (D2b section 4).
- **Chip-wide events.** The thermal cycle, the 2026-07-02 incident and the 2025-03 step are events, not drift, so keep them out of drift fits. Every qubit needs recalibrating after a thermal cycle (D1a, C2 section 4).
- **The 2025-03 step.** It's at the untracked DS2 write of 2025-03-18 19:06:53 and the first run after it, so the 2025-03-18 break separates it from the strata on either side. It isn't at the 2025-03-20 break (C2 section 4, D1b section 9).
- **Shared fractions.** Only read them on clean rows. Read a level $R^2$ next to its furthest run: in D1a one early run gives an $R^2$ of 0.994 on its own (D1a section 6).
- **Large f01 moves.** Label moves of 100 kHz or more with `series.f01_moves`, and analyse clean and controller-affected moves apart. Most large moves sit next to a warned run (D2a section 1).
- **KPIs at moves.** The after KPI is censored much more often at large moves, so no KPI change there doesn't rule out loss (D2a section 5).
- **Counting large moves.** Count them outside the incidents and flagged f01 rows, or the 2026-07-02 incident dominates the count (D1b section 8).
- **Move rate.** Estimate it per qubit and window, and compare a window against the rate by quarter. The windows just before the thermal cycle are a quiet stretch, not a baseline (D2a section 8).

### Ramsey T2\*

- **Match on fit quality.** Quiet-run T2\* z rises with r2. Only compare classes after matching on r2, and check the worst-fit third on its own, since the dip is there (D2a section 2).
- **Source.** Decode T2\* from the Ramsey payloads, as D2a does. The old `ramsey_f01_t2star.parquet` had broken values and has been removed (D2a).

### calibrate\_1q warnings and DRAG zero

- **Warnings as events.** Classify a freq warning before reading it as a device event. Most are DRAG at zero or a carried offset, not the qubit moving (D3a section 2).
- **Warning prediction.** Predict the next serious warning from persistence alone. Adding the qubit's past rate or the size of recent corrections makes it worse on all rows, and on fresh rows no gain shows (D3c sections 3 and 4).
- **Transition tests.** Use fresh rows (no serious warning in the window), not only clean rows. Clean rows still contain the history (D3c section 2).
- **DRAG-zero episodes.** Leave them out of fRB analyses. Q26's ok share is 0.003 during its episode (D3b section 2.1).

## Section key

<details>
<summary><b>Where each tag points</b></summary>

| Tag | Section |
|---|---|
| A1 | [Clock](phases/01_eda.md#a1-clock) |
| A2 | [Entity IDs](phases/01_eda.md#a2-entity-ids) |
| A3 | [Run map and coverage](phases/01_eda.md#a3-run-map-and-coverage) |
| A4 | [Study periods, gaps and breaks](phases/01_eda.md#a4-study-periods-gaps-and-breaks) |
| B | [Profiling, all of stage B](phases/01_eda.md#b-profiling) |
| B1 | [DS4 fridge](phases/01_eda.md#b1-ds4-fridge) |
| B2 | [DS2 hardware models](phases/01_eda.md#b2-ds2-hardware-models) |
| B3a | [Activity and linkage](phases/01_eda.md#b3a-activity-and-linkage) |
| B3b | [Sweep settings](phases/01_eda.md#b3b-sweep-settings) |
| B3c | [1q noise](phases/01_eda.md#b3c-1q-noise) |
| B3d | [KPI payloads](phases/01_eda.md#b3d-kpi-payloads) |
| B4 | [DS3 benchmarking](phases/01_eda.md#b4-ds3-benchmarking) |
| C | [Temporal analysis, all of stage C](phases/01_eda.md#c-temporal-analysis) |
| C1 | [DS3 KPIs over time](phases/01_eda.md#c1-ds3-kpis-over-time) |
| C2 | [1q parameters over time](phases/01_eda.md#c2-1q-parameters-over-time) |
| C3 | [DS4 MIX over time](phases/01_eda.md#c3-ds4-mix-over-time) |
| D1a | [Shared or local f01](phases/02_hypotheses.md#d1a-shared-or-local-f01) |
| D1b | [KPI structure](phases/02_hypotheses.md#d1b-kpi-structure) |
| D2a | [Large f01 moves and coherence](phases/02_hypotheses.md#d2a-large-f01-moves-and-coherence) |
| D2b | [A jump model for f01](phases/02_hypotheses.md#d2b-a-jump-model-for-f01) |
| D3a | [Ladder warnings and leftover error](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) |
| D3b | [The DRAG-zero trap](phases/02_hypotheses.md#d3b-the-drag-zero-trap) |
| D3c | [Warning streaks](phases/02_hypotheses.md#d3c-warning-streaks) |

</details>
