# Findings

This page gives the main outcomes so far, one line per notebook. The phase docs have the numbers and the evidence: [Phase 01](phases/01_eda.md) for the data, [Phase 02](phases/02_hypotheses.md) for the hypotheses. Each row links to its section there. The rules that every analysis follows are in [working_rules.md](working_rules.md).

## Contents

- [Findings](#findings)
  - [Contents](#contents)
  - [Status values](#status-values)
  - [Caveats](#caveats)
  - [Phase 01: Exploratory data analysis](#phase-01-exploratory-data-analysis)
  - [Phase 02: Hypotheses](#phase-02-hypotheses)
  - [Candidate changes](#candidate-changes)
  - [Open questions](#open-questions)

## Status values

| Status | Meaning |
|---|---|
| Done | The notebook answered its question. |
| Holds | The hypothesis passed a test that could have failed it. |
| Partly holds | Some parts passed and some didn't. The outcome says which. |
| Rejected | The hypothesis failed its test. |
| Open | No plan yet. |
| Planned | A notebook or test is planned. |

## Caveats

These apply to every outcome below.

- **One device, one thermal cycle.** Every "after the thermal cycle" result rests on a single event.
- **Replays, not live tests.** The candidate changes are scored on the archive. None has run on the device yet.
- **Values are censored.** The routines reject some updates and DS3 gates out bad fits. So a missing or unchanged value often means "rejected", and the kept values are biased. We drop entities censored above 10%, or model the censoring.
- **DS3 measures the recalibrated device.** Benchmarking runs straight after calibration, so DS3 never sees how far the device drifted before it.
- **Some windows were chosen from the data.** C1 found the 2025-12 and 2026-07 steps in the same KPIs that D1b then tested. D2b chose its 50 kHz setpoint threshold after seeing the test intervals. Results that rest on these choices need new data to confirm them.
- **Noise sets the resolution.** We only analyse a variable at lags where it moves beyond its noise. For several variables (drag, freq, T2, fRB) that is longer than one run.

## Phase 01: Exploratory data analysis

| # | Question | Outcome | Status |
|---|---|---|---|
| [A1](phases/01_eda.md#a1-clock) | Are all sources on the same clock? | Yes. Every source is on UTC, with no seasonal shift. | Done |
| [A2](phases/01_eda.md#a2-entity-ids) | Do qubit and coupling IDs mean the same thing in every source and over time? | Yes, so sources join on them directly. Couplings are directed. Q23's month-long excursion in 2025 is the calibration losing the qubit, not a relabelled qubit; its level change across it is open. | Done |
| [A3](phases/01_eda.md#a3-run-map-and-coverage) | Can each routine run be traced through DS1, DS2 and DS3? | Yes, from 2025-01-01, and the values agree along the way. A warning isn't a rejection, and Ramsey `new` isn't always the applied f01. | Done |
| [A4](phases/01_eda.md#a4-study-periods-gaps-and-breaks) | When does each source have data, and where did the process change? | The study period depends on the sources in use. Three outages stop every routine. Code releases, DS3 gating eras, coupling reversals and the ends of the 2025 outages are breaks. All dates are in `periods.py`. | Done |
| [B1](phases/01_eda.md#b1-ds4-fridge) | Is the fridge a signal to correlate against? | Not yet. MIX moves beyond its noise over days to weeks, but no cold epoch is long enough to test it. For now the fridge marks events, mainly the one thermal cycle. | Done |
| [B2](phases/01_eda.md#b2-ds2-hardware-models) | How does the controller change the hardware model? | It rarely rejects an update, but the rejections fall on a few entities. 1q amp has a chip-wide offset each run. Steps got larger after the thermal cycle. | Done |
| [B3](phases/01_eda.md#b3-ds1-measurements) | How do DS1 rows link to runs, and how noisy are the 1q parameters? | Rows link to runs by id, and few sweep settings change. The fit error understates 1q noise, so we read signal against a larger noise estimate. amp only makes sense as amp × drive scale. The payloads fill most of DS3's gaps. | Done |
| [B4](phases/01_eda.md#b4-ds3-benchmarking) | Which DS3 KPIs can we trust, and do they move beyond their noise? | One gate works in every era. Censoring is often above 10%, and for fCX it isn't random. T1 and fRO move beyond their noise from run to run. fCX, fRB and T2 mostly don't. | Done |
| [C1](phases/01_eda.md#c1-ds3-kpis-over-time) | How do the KPIs change over time? | In chip-wide steps, not slow drift. All five step in December 2025 and July 2026, and nothing in the register explains either step. | Done |
| [C2](phases/01_eda.md#c2-1q-parameters-over-time) | How do the applied 1q values change over time? | Also in steps. freq drops chip-wide across the 2025-03 outage and the thermal cycle. amp × drive scale moves as one chip and is lower in the evening. | Done |
| [C3](phases/01_eda.md#c3-ds4-mix-over-time) | How does the fridge temperature change over time? | Slowly, over weeks. There's too little data to call anything a trend. | Done |

## Phase 02: Hypotheses

| # | Hypothesis | Outcome | Status |
|---|---|---|---|
| [D1a](phases/02_hypotheses.md#d1a-shared-or-local-f01) | Once the incidents are out, f01 moves qubit by qubit, with a small chip-wide part. | Nearly all chip-wide f01 change comes from two incidents and the 2025-03 step. Otherwise each qubit moves on its own, and the fridge doesn't explain the small shared part. | Holds |
| [D1b](phases/02_hypotheses.md#d1b-kpi-structure) | The KPIs belong to each element, and the chip moves as a whole only at process events. | Differences between elements dominate. The 2025-03 outage and the thermal cycle step fRO, the drive frequency and drag. The 2025-12 and 2026-07 changes are small, and nothing outside the KPIs records them. | Holds for the split, partly for the chip-wide steps |
| [D2a](phases/02_hypotheses.md#d2a-large-f01-moves-and-coherence) | Large f01 moves are frequency switching, not bad fits, energy loss or charge noise. | Most large moves come from the controller. The clean rest are real f01 changes on a few qubits, not charge noise or shared electronics. Their T2\* dip lasts one run and comes with poor fits, and they don't switch back and forth. Censored KPIs leave loss open. | Rejected for most moves, open for a few qubits |
| [D2b](phases/02_hypotheses.md#d2b-a-jump-model-for-f01) | f01 is a random walk with rare jumps and outliers, and that model gives a better f01 setpoint. | The jump model predicts the next run well, but overstates how far f01 moves over weeks. Its setpoint lowers the typical error. A few large errors remain. | Partly holds |
| [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | The ladder warnings come from the routine itself, and the error they leave costs the 1q gates little. | Most warnings are DRAG at zero or a carried frequency offset. The leftover error costs little, even with weekly calibration. Applying the Ramsey value on offset-carried runs removes most large frequency errors. Coherence explains about a quarter of the 1q error. | Holds |
| [D3b](phases/02_hypotheses.md#d3b-the-drag-zero-trap) | Qubits get stuck at DRAG zero after a reset, it costs 1q RB, and a change to the reset prevents it. | Every trap starts with a reset to zero, and from zero the routine can't recover. A stuck qubit loses almost all its usable 1q RB, for up to a year. Not resetting looks like the fix. | Holds |
| [D3c](phases/02_hypotheses.md#d3c-warning-streaks) | Serious warnings come in streaks, and the streak predicts the next one better than recent corrections do. | The number of runs since the last serious warning predicts the next one well. Recent corrections don't add to it. The streaks themselves aren't studied yet. | Holds for prediction |

## Candidate changes

Changes to the calibration routines that Phase 02 supports. Each needs a live test in Phase 03.

| Change | From | Next step | Status |
|---|---|---|---|
| Don't reset DRAG to zero. Warn only, or clip to the threshold. | [D3b](phases/02_hypotheses.md#d3b-the-drag-zero-trap) | Live test | Planned |
| Apply the run's own Ramsey value when a frequency offset is carried. | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | A/B test with circuits that have idle time | Planned |
| Set f01 with a gain of 0.25, or with the jump-model hybrid. | [D2b](phases/02_hypotheses.md#d2b-a-jump-model-for-f01) | A deployment note, then a live comparison against the current rule on a few qubits | Planned |
| Run `calibrate_1q` less often, for example weekly. | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | Add a guard for rare large changes first | Planned |

## Open questions

Phase 02 also tracks the [leads from Phase 01](phases/02_hypotheses.md#leads-from-phase-01) that are still open.

| Question | From | Next step | Status |
|---|---|---|---|
| Where is the readout error, and what drives it? | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | d4a | Planned |
| Where is the 2q error, and why do some couplings fail their fit in most runs? | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | d4b | Planned |
| Are the clean large f01 moves on Q21 and a few other qubits a single defect switching? | [D2a](phases/02_hypotheses.md#d2a-large-f01-moves-and-coherence) | Track one qubit's f01 about once a minute | Planned |
| How long do warning streaks last, and what starts and ends them? | [D3c](phases/02_hypotheses.md#d3c-warning-streaks) | - | Open |
| What moves the KPIs in 2025-12 and 2026-07? | [C1](phases/01_eda.md#c1-ds3-kpis-over-time), [D1b](phases/02_hypotheses.md#d1b-kpi-structure) | A test on data that C1 didn't search | Open |
| Why does the jump model overstate f01 moves over weeks? | [D2b](phases/02_hypotheses.md#d2b-a-jump-model-for-f01), [D2a](phases/02_hypotheses.md#d2a-large-f01-moves-and-coherence) | Refit the jump model on clean moves only, since most large moves come from the controller | Open |
| What makes up the 1q error that coherence and calibration don't explain? | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | - | Open |
| Do the FineFrequency steps improve on the Ramsey value? | [D3a](phases/02_hypotheses.md#d3a-ladder-warnings-and-leftover-error) | An independent frequency measurement | Open |
