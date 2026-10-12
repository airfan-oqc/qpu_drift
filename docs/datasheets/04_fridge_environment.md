# Datasheet 4: Fridge/Environment

This dataset has environmental and operational readings from the cryogenic system of the Toshiko LX2 QPU. It includes stage temperatures, pressures, and compressor and pump health metrics, sampled on a coarse regular grid. The readings come from the LX2 fridge watchdog exporter and were exported from Grafana (section 3). They're collected for monitoring, not for modelling, so treat the data as found data. This sheet describes what's in the data. Open questions about how it's collected are in section 9.


## 1. Overview

| Field | Value |
|---|---|
| QPU ID | `qpu:jp:3:673b1ad43c` |
| Source name | LX2 fridge watchdog metrics (Prometheus) |
| Source of truth | Prometheus, read through Grafana at `https://oqc.grafana.net/`. The series are selected by `job=~"fridge-watchdog.*"` and `instance="lx2"`. |
| Raw export | One wide Grafana CSV (about 50 MB in this export). It's large and owned by Grafana, so it isn't committed. Section 3 shows how to export it. |
| Layout | Column 0 is the time (epoch ms). Every other column is one `(metric, k8s labels)` series, so the raw file is very wide (3,356 columns in this export). |
| Logical metrics | 138 distinct Prometheus `__name__` values in this export. The other columns are pod replicas, `istio-proxy` sidecar copies and label variants, which are mostly one-hot state and alert labels (section 5). |
| Clean table | One column per physical channel, built by `uv run build-fridge` (49 channels in this export, section 6). |
| Time span | Depends on the export. This export covers 2025-08-01 12:00 to 2026-09-03 12:00 UTC (about 13 months). |
| Cadence | A regular 6 h grid with a few gaps (section 5). |
| Unit | One row is one timestamp. The data is device-level (one fridge). |
| Join key | The timestamp only. There's no qubit or coupling key, because the data is device-level. |
| Producer | `oqc-watchdog` pods (`watchdog-lx2-*`) poll the fridge and expose its readings as metrics. Prometheus scrapes the pods, and Grafana exports the history to CSV. |

Counts in this sheet come from the export used to write it (downloaded on 2026-09-03). A newer export will give different numbers, but the rules stay the same.

## 2. Motivation and intended use

This data exists for operational monitoring and alerting. The fridge and its cooling plant report to a Prometheus watchdog, and Grafana can export the history. Many of its series are there for alerting: the `threshold_*` alarm limits, the `*_state_fridge` status series and the `GRAFANA_ALERTS` flags (e.g. "Compressor P5 Pressure Warning", "Fridge Is In Manual Mode"). The data wasn't collected with modelling in mind.

The data is most useful as background environmental readings, each with a timestamp, that you can align with any other time-stamped source. At a regular cadence, it records the fridge's physical state: stage temperatures (mixing chamber, still, cold plate, PT1 and PT2), gauge and compressor pressures, and the health of the compressors and pumps (flow, speeds, motor power and current, and bearing, oil and water temperatures). It also records the fridge's categorical status. The cadence is set by the Grafana export query (section 3). This sheet lists which channels exist, what state they're in, and how to load them cleanly.

## 3. Provenance and collection

The watchdog pods (`watchdog-lx2-*`) poll the fridge and expose its readings as metrics, which Prometheus scrapes. These are the steps used to export the data in this repo from Grafana (on 2026-09-03):

1. Open `https://oqc.grafana.net/` and select `Explore` in the menu on the left (or search for "Explore").
2. Switch the toggle from Builder to **Code** and enter this query:
   ```promql
   {job=~"fridge-watchdog.*", instance="lx2", __name__!~"asserts.*"}
   ```
   (`asserts.*` are Grafana's own meta-metrics, so they're excluded.)
3. Set the **time range** to the full available history. For this export it was:
   ```json
   {"from":"2025-07-30T23:00:00.000Z","to":"2026-09-03T22:59:59.000Z"}
   ```
   These bounds are local days in BST. The data starts at 2025-08-01 12:00 UTC, the earliest available. It ends at 12:00 UTC on the export day, because the 18:00 mark hadn't happened yet.
4. Select **Run query**, then open the Query inspector. Use Stats to check the result (series count, rows and size in bytes).
5. On the Data tab, choose **Series joined by time**. This outer-joins every series on one shared timestamp column, which gives the wide table. Then **download** the CSV.

> [!NOTE]
> Under Data options you can turn on "Formatted data" or "Excel-compatible CSV". This export leaves both off. The plain export keeps the raw epoch-millisecond timestamps and the full-precision floats exactly as Prometheus stored them, and pandas reads it directly.

<details><summary><b>How Prometheus fills each cell (and why there's no variability below 6 h)</b></summary>

The query in step 2 is a plain series selector run as a range query. Grafana runs it at a step of about 6 h, which it picks from the time range and the panel width. For a plain selector, each cell is a single instant reading, **not** a window average:

- **One sample per mark.** Prometheus returns the most recent scrape at or before each 6 h mark. Every scrape in between is discarded.
- **Missing cells for stale series.** A default 5-minute lookback applies. If no scrape happened in the 5 minutes before a mark, the cell is `undefined`, not the previous value.
- **No variability within 6 h.** Any spread within a window is dropped at query time, so it isn't in the file.

If you want the spread or oscillation amplitude per window, change the query in step 2 (e.g. `stddev_over_time`, `min_over_time`, `max_over_time`).

</details>

## 4. Composition and structure

**Format and encoding:**
- Each column header is a **Prometheus label set**, not a plain name, for example `{__name__="temperature_DRI_MIX_CL_DRI_MIX_S", container="oqc-watchdog", pod="watchdog-lx2-..."}`. The metric name is in the `__name__` label, next to the other labels. Labels that every series shares (e.g. `job`, `instance`) aren't in the headers. Grafana seems to drop common labels from series names.
- To get the metric name, extract the `__name__` value with a regex such as `__name__="([^"]+)"`. The quotes in the header are CSV-escaped as `""`, so the parser has to handle them. Most CSV readers do this automatically.
- **Missing values** are the literal string `undefined`, not an empty cell. Read the file with `na_values=["undefined"]` to parse them as `NaN`. `build-fridge` does this (section 8).
- The export has **no unit metadata**. For now, units are inferred from the channel name and magnitude. They still need to be confirmed with the cryogenics team (section 9).
- You can also change the Grafana query to export only the metrics you need. This sheet covers every stored metric, to give the full context.

**Structural schema**

| Element | Meaning |
|---|---|
| Column 0 (`Time`) | Epoch milliseconds (milliseconds since 1970-01-01 UTC) |
| Columns 1..N | One Prometheus series each: the metric in `__name__`, plus the labels that identify the series (`container`, `pod` and others, depending on the metric) |

**Example instance**

One clean row, with selected channels:

```text
timestamp                        2026-01-22 06:00:00+00:00
temperature_DRI_MIX_CL_DRI_MIX_S 0.00893   # mixing chamber stage
temperature_DRI_PT2_S            3.94      # pulse-tube 2nd stage
temperature_DRI_STL_S            0.78      # still stage
pressure_OVC_PG_01               0.025     # units unconfirmed (the other pressures look like Pa)
speed_3CL_TP_3CL_TP_01           819
```

**Logical metrics (by family)**

This table groups the 138 metrics by what they measure. It shows how much of the export is continuous sensor data, and how much is setpoints, counters and exporter bookkeeping. Counts are distinct `__name__` values, with replicas combined. "Physical" means a continuous sensor trace of a physical quantity. The non-physical families can still be useful for operations. For example, the `operating_hours_*` counters track total equipment runtime, and the `threshold_*` setpoints give the configured alarm limits.

| Family | Logical metrics | Physical | Notes |
|---|---:|:---:|---|
| Temperature (including plant temperatures) | 26 | yes | There are two groups. The dilution-fridge stage temperatures (mixing chamber, still, cold plate, PT1 and PT2, plus `temperature_SRB_GGS_*`) are the core cryogenic channels. The rest are health temperatures for the compressor and pump plant (bearing, oil, motor casing, pump casing, power stage, inlet and outlet water, electronics). |
| Pressure (including compressor high and low sides) | 12 | yes | Eight gauge pressures and four pulse-tube compressor pressures. |
| Flow, speed, motor power, motor current, heater power | 16 | mostly | Pump-plant readings. Cleaning drops seven channels that are flat (section 6). The 6 heater rails read a constant 0 during the first 4 months or so, when they report, and then stop. `speed_3CL_FP_01` is constant whenever the fridge is cold. |
| Voltage (PSU and UPS) | 2 | yes | These only report for the first 4 months or so, but they're 100% complete in that window, so both are kept (section 6). |
| Threshold_ | 32 | no | Alarm setpoints (the configured minimum and maximum limits for speeds, motor powers and so on). They're constant, except the four `threshold_pressure_3CL_PG_05_*` and `threshold_pressure_3CL_PG_06_*` limits, which each change once on 2025-08-14. |
| State (`*_state_fridge`, `recognised_state_fridge_info`, `Circulating_State`, valve and pump states) | 23 | no | The categorical fridge status. The three `*_state_fridge` metrics are one-hot encoded: each series is one named state, stored in a label, with a value of 0 or 1. Exactly one of them is 1 in each row. `recognised_state_fridge_info` works the same way, except that only the active state's series reports (always 1), and the others are `undefined`. `Circulating_State` and the 18 `state_*` valve and pump metrics are single binary series with no state label. |
| Operating_hours_ | 7 | no | Runtime counters. Their rate of change gives the equipment duty cycle. They mostly increase by one hour per hour, but they have three kinds of break. Some rows read 0 and recover on the next row (the glitch rows in section 6). Some counters freeze for a few weeks and then jump forward to catch up. Some restart from a lower value, for example after a pump has been off. So use the increase between nearby rows, and skip rows where the value drops or jumps. |
| Alerts, up, python_info | 3 | no | Infrastructure flags: `GRAFANA_ALERTS`, the Prometheus `up` scrape-health flag, and the `python_info` exporter build info. |
| Process_, scrape_, python_, power_enabled_ | 17 | no | Exporter internals (process, scrape and Python-runtime metrics), and the boolean `power_enabled_*` heater switches, which read 0 whenever they report. |

<details><summary><b>Fridge-stage temperatures</b> (core cryogenic channels)</summary>

The dilution-fridge stage temperatures are listed separately here, because these stages are closest to the qubits. Typical values in this export:

| Channel | Median (cold rows) | p05 to p95 (cold rows) | Warm-up max | Reading |
|---|---|---|---|---|
| `temperature_DRI_MIX_CL_DRI_MIX_S` (mixing chamber) | 9.10 mK | 8.63 to 9.97 mK | 44.96 K | base stage (qubit stage) |
| `temperature_DRI_CLD_S` (cold plate) | 93.2 mK | 92.4 to 93.7 mK | 150 K | |
| `temperature_DRI_STL_S` (still) | 0.78 K | 0.771 to 0.784 K | 189.1 K | |
| `temperature_DRI_PT2_S` (4 K stage) | 3.90 K | 3.83 to 3.96 K | 299 K | pulse-tube 2nd stage |
| `temperature_PTR1_PT2_S` (4 K stage) | 3.56 K | 3.52 to 3.61 K | 299 K | pulse-tube 2nd stage |
| `temperature_DRI_PT1_S` | 48.6 K | 44.2 to 51.0 K | 299 K | pulse-tube 1st stage |
| `temperature_PTR1_PT1_S` | 42.6 K | 40.9 to 45.8 K | 299 K | pulse-tube 1st stage |

> [!NOTE]
> **Zeros in the cold rows.** STL, PT1 and PT2 (both DRI and PTR1) read exactly 0 in 96 of the 1,452 cold rows in this export. The build sets these to NaN (section 6), so their cold-row statistics above use the other 1,356 rows.

The units here are inferred from magnitude. The stage temperatures and most plant temperatures are in kelvin. The bearing, casing, power-stage and most electronics temperatures are whole-degree values ending in `.15` (e.g. 308.15 K), so they were probably converted from integer °C. The oil temperatures and `electronics_temperature_3CL_FM_01` have finer steps, but they're also about 300 K. `inlet_water_temperature_*` (about 20) and `outlet_water_temperature_*` (about 29 to 31) can only be °C.

Pressures look like Pa, not mbar. `pressure_3CL_PG_04` peaks at 100,380, which is about 1 atm in Pa. The compressor high and low sides read about 2.2e6 and 7.7e5 (about 22 and 7.7 bar). The PG_05 and PG_06 alarm thresholds (135 to 165 since 2025-08-14) only match readings of about 1.2e5 if the thresholds are in kPa. The units of flows, speeds and power aren't confirmed yet (section 9).

</details>

## 5. Distribution and statistics

- **The data is on a regular 6 h grid.** The median and minimum gap between rows are both 6 h. There's one long break of about 21 days and two short gaps (18 h and 36 h). The 6 h step comes from the export query, not from the fridge (section 3).
- **Density only counts the rows that exist.** So it doesn't include the gaps. Most of the missing grid marks are in the 21-day gap.
- **Every logical metric appears as many raw columns (replicas).** The watchdog is redeployed over time, and Prometheus keeps one series per pod that ever ran. Each of these series is sparse, because only the live pod reports at any time. This comes from how the data is collected, and it isn't real duplicate data. Section 6 combines each physical metric back into one clean series.
- **After combining, most channels are almost complete.** The plant channels with 18 replicas are almost complete across the full record. The stage temperatures are less complete, because their invalid zeros are cleared (section 6).
- **Some channels only cover the first months.** The channels with 6 replicas stop after about four months, but they're complete until then (section 7 lists them).
- **One-hot state metrics turn into many columns.** Each `*_state_fridge` metric is one-hot encoded, with one series per possible state value. The whole set is then repeated for every pod replica. Section 6 drops these, because they aren't physical channels.

<details><summary><b>Numbers for this export</b></summary>

- **Span.** 2025-08-01 to 2026-09-03 (about 13 months), with 1,503 timestamps.
- **Gaps.** The long break is 504 h. The short gaps are 18 h (two missed marks) and 36 h (five missed marks). The full 6 h grid over the span has 1,593 marks, and 1,503 of them (94%) are present.
- **Density.** The clean table has 49 channels and 1,503 rows. Overall cell density is 0.881. The full-coverage channels are 93% to 99.9% dense. The eight early-only channels (section 7) are about 33% dense over the full record, which lowers the mean.
- **Coverage after combining.** The 18-replica plant channels are 99.8% to 99.9% complete. The stage temperatures are lower: MIX 97.8%, CLD 98.0%, and STL, PT1, PT2 and `SRB_GGS` 93.5%.
- **One-hot size.** `minor_state_fridge` has 52 possible states and 19 raw series (the 18 watchdog pods plus the `istio-proxy` sidecar). That gives **988 columns** for one logical metric.

</details>


## 6. Preprocessing and cleaning

The build ([src/qpu_drift/sources/fridge/clean.py](../../src/qpu_drift/sources/fridge/clean.py), run with `uv run build-fridge`, section 8) turns the very wide raw export into one clean column per physical channel. It doesn't change the raw file. Steps 2 and 3 only flag rows. No rows are removed from the parquet, and you apply the flags when you load it.

### Step 1: channel selection and structural cleanup

1. **Pick the physical channels.** The build maps each raw column to its `__name__` and keeps the physical families from the section 4 table. In this export that reduces 138 metrics to 56 candidates.
2. **Combine pod replicas.** Each channel is split across every pod that ever ran, but only one pod is live at a time, so the build merges them back into one series. If two pods ever overlap, the build uses the newest pod, not the median, because a median of two readings can produce a meaningless midpoint. Pods never overlap in this export, so this is only a safeguard. The build drops the `istio-proxy` sidecar copies first, because they don't match the watchdog readings.
3. **Clear invalid zeros.** An exact 0 in any temperature channel becomes NaN. Zeros in other channels are kept, because a stopped pump can really read 0 speed, flow or power. No kept channel reads exactly 0 in a cold row (step 2).
4. **Drop dead or flat channels.** A channel is kept if it's dense over the full record, or almost complete within its own coverage window. The second rule keeps the early-only channels. A kept channel also has to vary while the fridge is cold, so warm-up spikes can't keep a channel that's otherwise flat.
5. **Write the outputs:** `channels.parquet` (the clean table) and `channel_catalog.parquet` (one row per candidate, with the reason it was kept or dropped).

**Result in this export:** the 3,355 series columns give 138 metrics and 56 candidates, and the build keeps **49 channels**. All 7 dropped channels are flat while cold: the six heater rails (constant 0) and `speed_3CL_FP_01` (constant 80). Flat doesn't mean useless. The rails show that the heaters are off, and the pump speed marks the warm-up. But neither has any variation to use in operating-regime analysis. If you need them, read them from the raw export.

<details><summary><b>Step 1 details</b> (sidecars, zero counts, keep thresholds, catalog columns)</summary>

- **Sidecars:** the `istio-proxy` series only cover 44 rows (2026-03-05 12:00 to 2026-03-16 06:00, the lifetime of one pod). 41 of the 42 comparable channels disagree with the watchdog on some of those rows (only `speed_3CL_FP_01` always matches). The largest gaps come during the March warm-up, when readings change fast: up to about 5-6x for `pressure_3CL_PG_04` and `_PG_06`.
- **Zeros:** 662 cells are cleared. 643 are in the stage channels (the March 2026 warm-up and the three pod-restart runs, section 7), and 19 are in plant temperatures (the glitch rows at 2026-03-11 06:00 and 2026-08-04 12:00). On those two rows some pressures also read exactly 0, which an absolute gauge can't do: the four compressor pressures on 2026-03-11, and all seven gauge pressures and the PTR_01 compressor pressures on 2026-08-04 (along with the flow and the pump speeds and motor powers). The build keeps these zeros, because both rows fail the cold mask anyway. Zeros are cleared before step 4, so they never count as variation.
- **Warm-up ceilings** (MIX 44.962 K in 7 rows, CLD 149.978 K in 5) are lower bounds, not readings. They still correctly mark the fridge as warm, so the build keeps them and the cold mask drops them.
- **Keep rule:** density of at least 50% over the full record, **or** density of at least 90% between `first_valid` and `last_valid`, where that window covers at least 15% of the record. The channel also needs `std_cold > 0`, measured over cold rows (step 2).
- **Catalog columns:** replica count, `zeros_to_nan`, `density`, `window_density`, `window_span`, `std`, `std_cold`, the kept flag, `drop_reason` (`sparse`, `flat_cold` or both), `first_valid` and `last_valid`.
- **Input checks:** the build stops if the export repeats a timestamp, because replicas are merged by timestamp. If the MIX or PT2 channel is missing, it warns and measures `std_cold` over all rows, so a warm-up spike can then keep a flat channel.

</details>

### Step 2: cold-row mask

During the warm-up cycle and a few exporter glitches, the fridge isn't at operating conditions, or the reading is meaningless. Those rows aren't useful for later analysis. The mask uses stage temperatures to decide. A row is cold when `0 < MIX < 0.05 K` and `PT2 < 10 K`. The PT2 check only matters if the MIX sensor reads in range while the fridge is actually warm.

In this export the mask keeps **1,452 of 1,503 rows** (96.6%). 49 of the 51 dropped rows are from the March 2026 thermal cycle, and the other two are exporter glitches. The mask is lenient on purpose, so tighten it for your analysis with `load_fridge` (e.g. `load_fridge(cold=True, mix_max=0.01)` keeps 1,385 rows).

<details><summary><b>Step 2 details</b> (what gets dropped, tuning, <code>Circulating_State</code>)</summary>

- **Dropped rows:** 33 where MIX is missing (32 cleared zeros and one all-NaN row), and 18 where MIX is above 1 K (rising to its 44.962 K ceiling). The thermal cycle runs from 2026-03-09 06:00 to 2026-03-21 18:00. The glitch rows are 2026-07-07 06:00 (all NaN, just before the 36 h gap) and 2026-08-04 12:00 (the stages read 0 on a new pod's first row).
- **PT2 check:** no row has MIX in range with PT2 ≥ 10 K, so the check changes nothing in this export. A missing PT2 passes. So the 96 pod-restart rows whose PT2 zeros were cleared (step 1) stay in, as long as MIX is valid.
- **Tuning:** `load_fridge` takes `pt2_max`, `mix_min`, `mix_max`, `mix_channel` and `pt2_channel`. Cold-row MIX never goes above 15.7 mK, so any `mix_max` above that has no effect.
- **`Circulating_State`** can't replace the mask. It reads 0 in 1,267 cold rows and 1 in 50 of the 51 masked rows (section 9).

</details>

### Step 3: cold epochs

The cold mask tells you whether a row is at operating conditions, but not which period of cold operation it belongs to. That matters because a thermal cycle can reset the fridge (and the chip), so rows on either side aren't directly comparable. `cold_epochs` numbers these periods. It starts a new epoch wherever consecutive cold rows are more than 24 h apart. Rows that aren't cold get no epoch. The epochs in this export are:

| Epoch | First cold row | Last cold row | Cold rows | Starts after |
|---:|---|---|---:|---|
| 0 | 2025-08-01 12:00 | 2026-03-09 00:00 | 879 | start of record |
| 1 | 2026-03-22 00:00 | 2026-06-11 18:00 | 328 | March 2026 thermal cycle |
| 2 | 2026-07-02 18:00 | 2026-07-07 00:00 | 18 | 21-day export gap |
| 3 | 2026-07-08 18:00 | 2026-09-03 12:00 | 227 | 36 h gap (plus the masked 07-07 06:00 row) |

Only the boundary between epochs 0 and 1 is a real thermal cycle. The other two are export gaps where the plant kept running, so they mark missing data, not a reset. Merge epochs 1 to 3 if you only want to split on thermal cycles. The mask also doesn't detect the slow cool-down to base temperature after a thermal cycle. Use the epoch as a grouping variable, or drop the first days of each epoch.

<details><summary><b>Step 3 details</b> (gap boundaries, cool-down)</summary>

- **Gap boundaries:** across both gaps, the pulse-tube and turbo-pump `operating_hours_*` counters increase by one hour per hour (503 to 504 h over the 21-day gap, 42 h over the 36 h gap). So the fridge most likely stayed cold. MIX reads 10.8 mK on the first row after the 21-day gap and falls to 9.4 mK within a day, so something probably disturbed it. Across the 36 h gap it stays at about 8.7 mK. A rule based on gap length can't separate the two cases here, because the March cycle leaves only 312 h between cold rows, which is shorter than the 504 h export gap.
- **Cool-down:** after the March cycle the daily median MIX is 13.8 mK on 2026-03-22, 11.4 mK on 2026-03-26, 10.0 mK on 2026-04-01 and about 9.5 mK by 2026-04-08. The cold-row median is 9.10 mK. Tightening `mix_max` treats this as a threshold problem, but it's a problem of time since the cycle.

</details>


## 7. Limitations and caveats

- **Zeros and ceilings aren't real readings.** During the March 2026 warm-up, MIX and CLD drop to exactly 0 or stay at a fixed ceiling. Six stage channels also read 0 for days to weeks after three watchdog pod restarts. The clean table already turns temperature zeros into NaN, and the cold mask drops the ceilings (section 6). If you work from the raw export instead, treat any temperature of exactly 0 as invalid, and combine the pod replicas first.
- **Some readings go stale.** Sometimes the exporter stops updating a few channels, and they repeat their last value for anything from one row to a few weeks. In this export each stretch happens while a fridge alert is active. The build doesn't detect or clear these readings. They're rare over the whole record (under 1% in this export), but in a short window they can be a large share of a channel's data. For example, one 19-day stretch is a third of the last cold epoch for five channels, so in that epoch about half of their neighbouring readings show no change at all. Treat a run of identical values that's much longer than usual for that channel as missing. The stretches in this export are listed under Specifics below.
- **Units aren't declared.** Temperatures are mostly in K, but the water temperatures are in °C, and pressures look like Pa (section 4). The other units aren't confirmed (section 9).
- **Timestamps are UTC.** Make sure the other side of any join is UTC too.
- **Join with a tolerance.** An as-of join or forward-fill with no limit will copy a reading across the 21-day gap or into a warm-up. Match to at most one step back, e.g. `pd.merge_asof(..., direction="backward", tolerance=pd.Timedelta("6h"))`.
- **There's no variability below 6 h.** Each value is a point sample (section 3). If oscillation amplitude matters, change the query.
- **Some channels stop early.** The 3 `motor_current_*`, the 2 `electronics_temperature_*`, `pressure_3SP_PG_01` and the 2 PSU and UPS voltages only report from 2025-08-01 to 2025-12-02. They're kept, so only use the rows between their `first_valid` and `last_valid`.
- **State metrics aren't documented**, so their operational meaning isn't confirmed (section 9).

<details><summary><b>Specifics</b> (zero runs, stale windows, early-only stop)</summary>

- **Warm-up:** MIX and CLD stop at fixed ceilings (section 6), while the other stages keep warming (maxima in section 4). In the raw export MIX reads exactly 0 in 2.1% of rows and above 1 K in 1.2%.
- **Pod-restart zeros:** STL, PT1, PT2 (DRI and PTR1) and `SRB_GGS` read 0 in 96 cold rows. These come in runs of 10, 27 and 59 rows, starting at the pod restarts on 2025-10-17, 2026-02-20 and 2026-08-04. 95 of the 96 rows are inside "Fridge Watchdog Appears Frozen" alert windows.
- **Stale windows:** each row lists the repeated readings, after the last real value.

  | Repeated rows | Cold rows | Channels | Alert active |
  |---|---:|---|---|
  | 2025-10-17 12:00 | 1 | STL, PT1, PT2 (DRI and PTR1) and `SRB_GGS` | "Fridge Watchdog Appears Frozen" |
  | 2026-01-12 18:00 to 2026-01-16 00:00 | 14 | the same six stage channels and the flow | "Fridge Watchdog Appears Frozen" |
  | 2026-02-20 00:00 to 2026-02-20 06:00 | 2 | the same six stage channels | "Fridge Watchdog Appears Frozen" |
  | 2026-07-16 18:00 to 2026-08-04 06:00 | 75 | both turbo-pump motor powers, `pressure_3CL_PG_02`, `_PG_03` and `pressure_OVC_PG_01` | "Fridge Not In Circulating State" |

  All but the January stretch end on a watchdog pod restart, and the pod-restart zeros above follow straight after. So in a new export, look for stale readings around these alerts and pod restarts. `motor_power_3CL_TP_3CL_TP_02` may also be stale in January: it holds one value for 16 rows, but never more than 6 elsewhere while the fridge is cold. Slow turbo-pump temperatures and speeds also hold steady during the stretches, but they hold steady outside them too, so they aren't listed.
- **`speed_3CL_FP_01`** only drops to 0 in 16 warm-up rows and in the 2026-08-04 12:00 glitch row.
- **Early-only stop** (2025-12-02 06:00): this isn't caused by a pod redeploy. The pod that was live at the time kept running until 2026-02-20 (section 9).

</details>

## 8. Access and usage

The clean outputs (`data/clean/fridge/`) are built from the raw Grafana CSV. That file is large and owned by Grafana, so it **isn't committed** to the repo. Follow the steps in section 3 to export it.

**1. Build.** Save your raw export as `data/raw/fridge_lx2_grafana_<date>.csv` (e.g. `fridge_lx2_grafana_20260903.csv`). Then run:

```bash
uv run build-fridge                 # uses the newest data/raw/fridge_lx2_grafana_*.csv
uv run build-fridge --raw path.csv  # or a specific export
uv run build-fridge --min-density 0 # no density floor, so only the flat-while-cold rule drops channels (same 49 kept in this export; section 6)
```

This writes `channels.parquet` (the clean table) and `channel_catalog.parquet` (the build details for each channel) to `data/clean/fridge/`. Both are zstd parquet files with build metadata (source export, build time and `min_density`).

**2. Load.**

```python
from qpu_drift.sources.fridge import cold_epochs, load_fridge

w        = load_fridge()                          # one row per timestamp, tz-aware UTC index
w_cold   = load_fridge(cold=True)                 # cold rows only (the step 2 mask)
w_strict = load_fridge(cold=True, mix_max=0.01)   # same, with a tighter MIX limit
epoch    = cold_epochs(w)                         # cold-epoch number per row, NA outside the mask
```

Or read the files directly, without the package:

```python
import pandas as pd

w   = pd.read_parquet("data/clean/fridge/channels.parquet").set_index("timestamp")  # one row per timestamp, tz-aware UTC index
cat = pd.read_parquet("data/clean/fridge/channel_catalog.parquet")                  # one row per candidate channel
```


## 9. Open questions
- **Heaters.** The heater channels (`power_DRI_*_H`, `power_SRB_GGS_*_H`) and their `power_enabled_*` switches read 0 throughout, so they seem to be switched off. Can this be confirmed? Why did they and the other early-only series stop on 2025-12-02 06:00?
- **Units.** The export declares no units. We need to confirm the units of the pressures (Pa, with thresholds in kPa?), the temperatures (water in °C and the rest in K?), and the flows, speeds, motor power, motor current and voltages.
- **Native sampling rate.** The 6 h grid is the Grafana export step, not necessarily the rate at which Prometheus scrapes the fridge (section 3). What is the real scrape rate? The Prometheus scrape config has the definitive value.
- **Subsystem abbreviations.** What do `DRI`, `PTR1`, `3CL`, `3SP`, `OVC`, `SRB`, `GHS`, `AV` and `AUX_UPS` stand for, and which physical parts do they refer to?
- **Categorical states.** What does each state mean for operations? `Circulating_State` is the clearest example. It reads 0 during normal cold operation. It reads 1 only around the March 2026 thermal cycle and during some of the watchdog problems in section 7 (pod restarts and stale stretches). The "Fridge Not In Circulating State" alert is active when it reads 1. Is the flag inverted, or does it mean something else?

> [!NOTE]
> **Native rate and variability (to do).** Export one series over a short window at fine resolution and check the spacing of the raw timestamps. If the fridge logs faster than 6 h, there are two ways to add features for the spread or oscillation amplitude within each window. One is to do the aggregation in the query (`stddev_over_time`, `min_over_time` or `max_over_time`). The other is to export again at close to native resolution over a shorter window and compute the spread in pandas.
