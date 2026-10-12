# Phase 02: Hypotheses

[Phase 01](01_eda.md) described the four sources and how their values change. This phase tests the hypotheses that Phase 01 raised, one notebook per hypothesis. Each notebook states its hypothesis at the top and ends with a verdict. The [findings](../findings.md) page summarises the verdicts and lists the candidate changes for Phase 03.

## Contents

- [Phase 02: Hypotheses](#phase-02-hypotheses)
  - [Contents](#contents)
  - [Approach](#approach)
  - [Leads from Phase 01](#leads-from-phase-01)
  - [D1. Chip-wide or local](#d1-chip-wide-or-local)
    - [D1a. Shared or local f01](#d1a-shared-or-local-f01)
    - [D1b. KPI structure](#d1b-kpi-structure)
  - [D2. f01 dynamics](#d2-f01-dynamics)
    - [D2a. Large f01 moves and coherence](#d2a-large-f01-moves-and-coherence)
    - [D2b. A jump model for f01](#d2b-a-jump-model-for-f01)
  - [D3. calibrate\_1q control loop](#d3-calibrate_1q-control-loop)
    - [D3a. Ladder warnings and leftover error](#d3a-ladder-warnings-and-leftover-error)
    - [D3b. The DRAG-zero trap](#d3b-the-drag-zero-trap)
    - [D3c. Warning streaks](#d3c-warning-streaks)
  - [D4. Error by operation](#d4-error-by-operation)
  - [Working rules](#working-rules)

## Approach

The notebooks are grouped into threads. Each thread has a number, and each notebook in it has a letter. Every analysis follows the [working rules](../working_rules.md), which hold the rules from both phases.

| Thread | Question | Notebooks |
|---|---|---|
| D1. Chip-wide or local | Does the chip move as a whole, or does each element move on its own? | d1a, d1b |
| D2. f01 dynamics | What are the large f01 moves, and how should we model and set f01? | d2a, d2b |
| D3. calibrate\_1q control loop | What do the warnings mean, what does the leftover error cost, and why do qubits get stuck at DRAG zero? | d3a, d3b, d3c |
| D4. Error by operation | Where are the readout and 2q errors, and what drives them? | planned: d4a, d4b |

> [!NOTE]
> **Section layout.** Each notebook section gives the hypothesis, the verdict and the outcome. A collapsed **Key numbers** table holds the numbers, with the notebook section that prints each one. **Open** lists what the notebook couldn't answer.

## Leads from Phase 01

Phase 01 recorded changes that it couldn't explain. This table tracks each one. The notebook sections in the second column have the numbers.

<details>
<summary><b>Leads and their status</b></summary>

| Lead | Raised in | Followed up in | Status |
|---|---|---|---|
| The chip-wide freq step across the 2025-03 outage | C2 section 4 | D1b section 9: the step is at the untracked DS2 write of 2025-03-18 19:06:53 | Closed |
| The 2025-02 step in amp × drive scale and drag | C2 section 4 | D1b section 9: it follows two drive-scale writes made by no run | Closed |
| Chip-wide KPI change points in 2025-12 and 2026-07 | C1 section 5 | D1b sections 4 to 8: no DS2, DS4 or register counterpart | Open |
| Step spreads, T2 noise, fRO and drag rise after the thermal cycle. T2 already rises in 2026-01 to 03, so compare windows next to the cycle, not whole strata | B2 section 5, B3c section 5, B4 section 5, C1 section 4, C2 section 4 | D1b section 4 (fRO step); D2a section 8: the f01 move rate just before the cycle is a quiet stretch, not a baseline | Partly followed |
| T2 above the 80 µs sweep, and T2 > 2 T1 | B4 section 3 | C1 section 6: the drift signs and the 2025-12-21 change point hold without them | Closed |
| `cr_scale` phase moves at the thermal cycle, against fCX | B2 section 5 | B4 section 5: rank correlation 0.241 (permutation p 0.269) | Closed, no link |
| The 2026-06 RB duration step | B3a section 3, B3b section 4 | B4 section 5: RB ratios 0.93 to 1.03 across it | Closed |
| fCX is missing not at random | B4 section 2 | Phase 01 working rule on censoring | Rule |
| 2024-10 change points in T2 and fRO, and an fCX fall, inside the `cr_u_stability` runs | B4 section 5, C1 section 5, C2 section 5 | Next: test the fCX part against the lag-1 upper bound before fetching `fetch_rb2q_rest.txt` | Open |
| fRO change points in 2025-06 and 2026-05 with no register event | B4 section 5 | Next: check them against the DS2 readout params, including `mean_z_map_args` | Open |
| fRO rises from 2026-04 | B4 section 5, C1 section 3 | Next: compare within stratum 13, against DS4 and DS2 | Open |
| fRO change points near `UNTRACKED_DS2` writes, a little above chance | B4 section 6 | Next: check which DS2 params those snapshots change | Open |
| DS1 rows outside any run on 20 days, with no register event | B3a section 5 | - | Open |

</details>

## D1. Chip-wide or local

This thread asks whether the chip moves as a whole or each element moves on its own. D1a looks at f01 and D1b at the DS3 KPIs.

### D1a. Shared or local f01

**Notebook:** [d1a_f01_shared_vs_local](../../notebooks/03_hypotheses/d1a_f01_shared_vs_local.ipynb)  
**Hypothesis:** once the incidents are out, f01 moves qubit by qubit, with a small chip-wide mode.  
**Verdict:** holds.

**Outcome.** Once the incidents are out, the chip hardly moves f01 as a whole. The shared part of each run's change is just above chance. It doesn't grow over 28 runs and has no slow 30-day part. A small shared level remains in a few segments, and the fridge doesn't explain it (sections 2, 3, 6 and 7). Nearly all the chip-wide variance comes from the two incidents and the 2025-03 step. Putting the incident rows back turns a share of a few per cent into an $R^2$ near 1 (sections 4 and 5). Each qubit's own drift is between bounded and a random walk, whichever noise level is removed (section 3).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Lag-1 shared share, clean rows | median 0.050 against a null of 0.046 (section 2) |
| Chip-wide sum of squares in the top 5 runs | 0.94 with all rows, 0.097 on clean rows (section 4) |
| Net f01 change across the thermal cycle | about −1.3 MHz per qubit, median (section 4) |
| Shared level before 2026 | chip sd 1.8 kHz against a local sd of 7.6 kHz (section 6) |
| Settled MIX against chip f01 | $r$ about −0.3 in epoch 0 only; many-channel $R^2$ at chance (section 7) |

</details>

**Open.** The small shared f01 level clears chance in 3 of 11 segments, all from 2025-09 on. Settled MIX follows it only in epoch 0, with the opposite sign in epochs 1 and 3. We don't know what drives it.

### D1b. KPI structure

**Notebook:** [d1b_kpi_structure](../../notebooks/03_hypotheses/d1b_kpi_structure.ipynb)  
**Hypothesis:** the KPIs are a property of each element, and the chip moves as a whole only at process events.  
**Verdict:** holds for the split, partly holds for the chip-wide steps.

**Outcome.** The KPIs are mostly a property of each element. Element rankings stay the same from one stratum to the next, and the chip-wide run part is a few per cent. Only fRO changes its ranking, across the thermal cycle (sections 1 to 3). The two process events, the 2025-03 outage and the thermal cycle, show up in every check. fRO, the drive frequency and drag all step, with untracked DS2 writes, and the thermal cycle also moves the fridge (sections 4 to 7). The 2025-12 and 2026-07 changes are weaker. fCX moves by about 10% and fRO by 5 to 7%. But C1 chose those windows from the same data, the placebo q is only 0.0988 to 0.129, and DS2, the register and the fridge show nothing at those dates (sections 4 to 7). Clean large f01 moves are absent around 2025-12 and ordinary around 2026-07, where controller-affected moves are at their highest (section 8).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Variance split, censoring at most 10% | element 0.487 to 0.79, element by stratum 0.0549 to 0.263, run 0.0136 to 0.0402 (section 1) |
| Within one stratum | element 0.452 to 0.921, run at most 0.0635 (section 2) |
| Rankings across strata | median $\rho$ 0.84 to 0.956; fRO across the thermal cycle 0.49 (section 3) |
| fRO step at the events | +46.9% thermal cycle, −9.75% outage, both q 0.0336 (section 4) |
| fCX step in 2025-12 and 2026-07 | −10.5% (q 0.0988) and +12.6% (q 0.115) (section 4) |
| Drive frequency at the events | −1.27e+03 kHz outage, −1.28e+03 kHz thermal cycle, q 0.0212 (section 5) |
| DS2 and fridge in 2025-12 and 2026-07 | DS2 placebo_p 0.202 or more, no register entry, fridge placebo_p 0.201 or more (sections 5 to 7) |
| Clean large f01 moves per qubit-day | thermal cycle 0.0282, before the 2025-03 outage 0.0216, against a reference median of 0.0022 and q95 of 0.0173; none around 2025-12; 0.0029 to 0.0121 around 2026-07 (section 8) |

</details>

<details>
<summary><b>Two Phase 01 leads checked in section 9</b></summary>

- **Where the 2025-03 freq step is.** Across every `calibrate_1q` gap of 2 days or more, the share of qubits whose f01 steps beyond `BIG_STEP` (289 kHz, the 99% placebo quantile) is 1 across 2025-03-09 08:00 to 03-19 08:00 (median −1.26e+03 kHz), against a placebo maximum of 0.0667. Without Q13, Q26 and Q27 the step is −1.43e+03 kHz with a share of 1. Run by run, the only run above the placebo maximum in 2025-03-01 to 04-05 is 2025-03-19 08:00:20 (median −1.25e+03 kHz). The runs at the 2025-03-20 and 03-28 breaks have share 0. Across the gap DS2 changes the drive frequency once, at 2025-03-18 19:06:53 (32 qubits, `UNTRACKED_DS2`). The DS2 change matches the applied step with a correlation of 0.997 and a median \|applied − DS2\| of 18.2 kHz. So the step is at the 2025-03-18 untracked write and the first run after it, not at the 2025-03-20 break, and it doesn't depend on the DRAG-zero qubits.
- **Which writes changed amp × drive scale and drag on 2025-02-12.** Three snapshots on that day are written by no run. 11:18:02 changes the drive scale on 17 qubits (median ln −0.348) and has no register entry. 13:40:14 (`UNTRACKED_DS2`) changes the X/2 amp, drive frequency and drive scale on 32 qubits (drive scale median ln +0.167). 17:34:10 changes one drive frequency. The `calibrate_1q` snapshots at 13:57:40 and 20:01:57 change the drive scale by a median ln 0.000276 and 0.00424. Against each qubit's last 7 runs before the outage, that day's `calibrate_1q` runs are at amp_x_scale +0.0195 and +0.0197 ln, and drag +1e-10 and +1.24e-10, against a median drag sigma of 1.66e-11. So the change follows the two no-run drive-scale writes, and one of them (11:18:02) isn't registered.

</details>

**Open.** What moves fCX and fRO in 2025-12 and 2026-07? A fair test needs data that C1 didn't search, so for now we treat both as weak.

## D2. f01 dynamics

This thread asks what the large f01 moves are, and how to model f01 between calibrations.

### D2a. Large f01 moves and coherence

**Notebook:** [d2a_f01_jumps_coherence](../../notebooks/03_hypotheses/d2a_f01_jumps_coherence.ipynb)  
**Hypothesis:** large f01 moves and their T2\* dip are frequency switching, not bad fits, energy loss or charge noise.  
**Verdict:** doesn't hold for most moves, open for a few qubits.

**Outcome.** We wanted to know why T2\* comes out low when f01 moves by 100 kHz or more. Most of these moves aren't the qubit: they sit next to a warned run, so the controller sets them (section 1). The clean rest are real f01 changes. They agree with the Ramsey value, they're far too large for charge noise, and qubits move independently, so shared electronics are out (sections 2, 9 and 10). Clean holds come with a T2\* and FineFrequency contrast dip, and each qubit's moves have one typical size. But clean holds don't return to earlier levels more than chance, and the T2\* dip lasts one run and sits in the worst-fit third (sections 2 to 7). The after KPI is missing too often at moves to rule out loss (section 5). The clean move rate after the thermal cycle is back near its early-2025 level; the windows just before the cycle are a quiet stretch (section 8). Most clean moves are on a few qubits, Q21 above all.

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Controller-affected large moves | 735 of 883 (section 1) |
| Ramsey change of 100 kHz or more at the move | 77% of clean moves, 34% of controller-affected ones (section 2) |
| T2\* dip in z, matched on r2 | −0.31 at clean moves, −0.43 at clean holds; −0.61 in the worst-fit third, none in the other two (section 2) |
| FineFrequency contrast in z at clean holds | −0.39 with the offset divided out; no dip at the longer delay (section 4) |
| After KPI censored, clean moves against quiet runs | T2 65% against 22%, T1 23% against 6% (section 5) |
| Holds back at an earlier level | clean 14 of 38 pairs against 11 shuffled; all 75 of 235 against 53 (section 6) |
| Clean move rate per run | 0.0013 and 0.0006 just before the thermal cycle, about 0.006 after; 0.0075 in 2025Q1 (section 8) |
| Largest charge dispersion | 0.046 kHz, against a smallest median move of 104 kHz (section 10) |

</details>

**Open.** One f01 per run is too coarse to catch a single TLS switching, so the direct check is a dedicated measurement on Q21 and Q7 (section 12). Among large moves the warned share rises as T2\* falls, so low coherence may set off the warnings; we haven't tested that. A fault on one qubit's own drive channel would also fit the clean moves, apart from the dip. The checks run many comparisons without a correction.

### D2b. A jump model for f01

**Notebook:** [d2b_f01_jump_drift_model](../../notebooks/03_hypotheses/d2b_f01_jump_drift_model.ipynb)  
**Hypothesis:** f01 is a random walk with rare jumps and outliers. That model says how stale a calibration gets, and its level makes a better f01 setpoint.  
**Verdict:** partly holds.

**Outcome.** The jump-and-outlier model M3 beats the Gaussian random walk on every qubit, in and out of sample. It gets the share of large moves right at the next run. It's wrong over weeks: at two weeks it predicts more than twice the large moves the data shows. Stage noise, a pull-back term and window fits don't close that gap. Its intervals are too wide before the thermal cycle and too narrow after it, and refitting on all past data helps. As a setpoint, the M3 hybrid and a gain of 0.25 lower the typical error, but a few large errors still set the overall rms. Keeping the old value on a warning makes it worse, because warnings come in streaks (sections 14 and 16).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| M3 against the Gaussian walk, in sample | best by AIC on 30 qubits, median ΔAIC −3346 (section 3) |
| Out of sample from 2025-12-20 | gain 1.434 nats per test row; 95% miss 0.031 before the thermal cycle and 0.1415 after it (section 4) |
| Share beyond 3 robust sd, data against M3 | 0.087 against 0.09 at lag 1, 0.183 against 0.427 at 14 days (section 5) |
| Labelled runs | 2,887 outliers (0.84 revert), 1,049 jumps (0.0308 revert) (section 6) |
| Allan deviation, jumps and outliers out | flat at about 5 kHz from 4 days, 4.83 kHz at 16.2 days (section 7) |
| Median change by FineFrequency stage | 4.45 kHz full ladder, 82 kHz one step short, 33.9 kHz two or more short (section 8) |
| Extensions against M3 | stage noise ΔAIC −40.33, pull-back +2.098; M4s gains 0.0047 nats out of sample (sections 10 and 11) |
| Expanding refit against the fixed fit, same search | +0.015 nats per row for M3 (25 qubits), 95% miss 0.102 to 0.083 (section 13) |
| Setpoint rms on core intervals, next-run noise out | 5.738 kHz producer's rule, 4.702 kHz gain 0.25, 4.373 kHz M3 hybrid (section 14) |
| Errors above 200 kHz | 2.06% of intervals, 99.5% of the squared error (section 14) |
| Window fits, share beyond 3 robust sd at 14 days | 0.291, against 0.193 in the data and 0.425 for the full-span fit (section 15) |

</details>

**Open.** Why does M3 overstate large moves at two weeks? A changing jump rate explains part of it (section 15). Small unlabelled jumps or a $1/f$ part could explain the rest. So could the controller: D2a finds that most large moves, and most outs and backs, are controller-affected, and M3 reads an out the next run undoes as a lasting step. Refitting M3 on clean moves would test this. The setpoint gains come from a replay of the archive, and the hybrid's 50 kHz threshold was chosen after seeing the test intervals, so the rule needs a live test (section 17).

## D3. calibrate\_1q control loop

This thread follows `calibrate_1q` from its warnings to what they cost. D3a explains most warnings, D3b follows the qubits that get stuck at DRAG zero, and D3c asks whether the next serious warning can be predicted.

### D3a. Ladder warnings and leftover error

**Notebook:** [d3a_calibrate_1q_ladders](../../notebooks/03_hypotheses/d3a_calibrate_1q_ladders.ipynb)  
**Hypothesis:** the ladder warnings are the routine's own mechanics, and the error left behind costs the 1q gates little at any cadence.  
**Verdict:** holds.

**Outcome.** Most warnings come from the routine's own rules, not the qubit. They are DRAG at zero, or a step-1 offset that ends up in a bad applied value (section 2). The error left behind costs little. Frequency offsets don't act like a real detuning. A per-pulse phase correction would remove under 1% of the fRB infidelity, so it isn't worth adding. The amplitude left by a short ladder is a few per cent of fRB at most. Running the routine weekly instead of twice a day would add about 1%, but this cost model leaves out rare large changes such as failed Ramsey fits (sections 4, 5 and 7). Coherence explains only about a quarter of the 1q infidelity, and readout is furthest above its coherence floor (section 6). Ramsey T2\* is well below echo T2. Read as slow noise, it barely changes that quarter. Read as fast noise, it would exceed the measured fRB infidelity on 12 of 27 qubits (section 6). The large errors come from offset-carried runs. Applying their own Ramsey value removes most of the applied errors above 200 kHz, one run ahead, and the typical error stays the same (section 8).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Warnings that are DRAG at zero, and offset carried | 771 and 673 of 1,948 (section 2) |
| Applied value against the next Ramsey | median 301 kHz for offset carried, 11.5 kHz for clean runs (section 2) |
| 0.1 µs step bias | a stable pulse phase plus the X/2 width; fits the 32 and 96 ns pulses, wider pulses drift off at step 3 (section 3) |
| Per-pulse phase correction | would remove a median 0.8% of fRB infidelity (section 4) |
| Leftover amplitude error | 0.6% of the median fRB infidelity, 2.7% with out-of-sweep runs at their edge (section 5) |
| Distance above the coherence floor | readout about 16 times, 1q 4, 2q 2 (section 6) |
| Ramsey T2\* against echo T2 | median 10.6 against 58 µs, on 27 qubits; coherence share 0.256 with echo T2, 0.258 with T2\* read as slow noise (section 6) |
| Extra cost of skipping runs | at most 1.2% of the chip median fRB, at one week (section 7) |
| Applied errors above 200 kHz, next run's Ramsey | 867 as run, 329 with Ramsey on offset-carried runs (0.62 removed, 0.36 to 0.80), 285 with Ramsey on every warning, 711 keeping the previous value; rms up to 200 kHz about 25 kHz under every rule (section 8) |

</details>

**Open.** Why does step 3 drift off the pulse-phase model for the wider X/2 pulses? What makes up the three quarters of the 1q infidelity that coherence and calibration don't explain (sections 3 and 6)? T2\* comes from 0.5 µs Ramsey traces, so a long Ramsey would make it more reliable. Whether the FineFrequency steps improve on the Ramsey value on clean runs needs an independent frequency measurement, because both references used are built from those two estimates (Next steps).

### D3b. The DRAG-zero trap

**Notebook:** [d3b_drag_trap](../../notebooks/03_hypotheses/d3b_drag_trap.ipynb)  
**Hypothesis:** qubits get stuck at DRAG zero through the first step after a reset, it costs 1q RB, and a change to the reset prevents it.  
**Verdict:** holds, with a refined cause.

**Outcome.** Every trap starts with a reset to zero by the spec check. From zero, the routine's estimates are unreliable: too large, too small or the wrong sign. So the first step is rejected run after run (section 3). While a qubit is stuck its 1q RB is almost all censored (the silent Q13 episode aside), and on Q16 and Q26 its couplings are affected too (section 2). All six traps started under the old code's 4 ns threshold. The current 9 ns code is safer, but it didn't release Q26 for almost a year, and it can't bring any reset qubit back in one step (sections 3.2 and 5). Keeping or clipping the value instead of resetting it looks like the fix (section 4).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Episodes | 17 on 7 qubits; the longest are Q13 (silent, 515.9 days) and Q26 (stuck, 354.3 days), both from the 2025-03-20 rollback (section 1) |
| Q26's fRB ok share | 0.950 before, 0.003 during, 0.821 after (section 2.1) |
| Resets | 114 on 8 qubits; 6 trapped, all under the old code, 773 runs at zero in total (section 3.2) |
| Estimates from zero | 1.6 to 4 times the value before at N = 1; on Q26 at N = 2, a median +5.09 ns against −3.81 ns before; 0.17 of the replaced value on escaped resets (section 3.3) |
| Wider first window | reset again on all 6 traps (section 4) |
| Current risk | Q26 reached 9.29 ns in 2026-07; Q22 reaches 9 ns in about a year on its trend (section 5) |
| Censoring elsewhere | Q22, Q33, Q15 and Q28 accept a new DRAG on 0.887 to 0.995 of runs, so it isn't frozen (section 6) |

</details>

**Open.** Why do the estimates from zero overshoot, undershoot or flip sign? Is Q22's drop in fRB censoring in 2026Q2 tied to its DRAG move (sections 3.3 and 6)?

### D3c. Warning streaks

**Notebook:** [d3c_warning_streaks](../../notebooks/03_hypotheses/d3c_warning_streaks.ipynb)  
**Hypothesis:** serious warnings are stuck controller states. They come in streaks with a few causes, and the streak itself, not the size of recent corrections, says when the next one is due.  
**Verdict:** holds for prediction. Streaks aren't studied yet.

**Outcome.** We wanted to know whether a qubit's warning history predicts its next serious (frequency or DRAG) warning, and whether recent corrections add to it. History wins. Runs since the last serious warning rank the next one at an AUC of 0.981 (section 2). A qubit's own warning rate doesn't predict its rate in a later period (section 2), and corrections lower the score on all rows (sections 3 and 4). On fresh rows, with no serious warning in the last 14 runs, corrections might add a little (0.014 to 0.042 across the tests). But every interval includes 0, because there are too few events to tell (sections 3 and 4).

<details>
<summary><b>Key numbers</b></summary>

| Check | Result |
|---|---|
| Panel | 36,554 eligible rows; test base rate 0.0702; Q26 has 0.361 of events (section 1) |
| Runs since the last serious warning | AUC 0.981 on all rows, 0.928 on clean now rows, 0.833 on fresh rows (section 2) |
| Qubit rate | AUC 0.346; training against test Spearman −0.149 (section 2) |
| Persistence alone against plus qubit and chip | 0.9819 against 0.9747 (section 3) |
| Raw corrections on fresh rows | +0.0160 [−0.0087, 0.0415] on 21 test events; +0.0296 [−0.0094, 0.0728] pooled over folds, 63 events (sections 3 and 4) |

</details>

**Open.** Do corrections help on fresh rows? That would take more fresh events than the archive has. How long do streaks last, what causes them and what ends them (Next steps)?

## D4. Error by operation

Planned. D3a found readout furthest above its coherence floor and the 2q gates closest, but several couplings fail their fit in most runs ([D3a](#d3a-ladder-warnings-and-leftover-error), section 6). D4a will look at readout and D4b at the 2q couplings: where the error is, and what drives it.

## Working rules

The rules from this phase are merged with the Phase 01 rules in [working_rules.md](../working_rules.md). Each rule there names the section here that holds its evidence.
