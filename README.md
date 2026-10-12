# Secondment project

## Repository structure

```
secondment/
├── docs/
│   ├── phases/       # one file per project phase, written as work proceeds (the process record)
│   ├── datasheets/   # one datasheet per data source, after *Datasheets for Datasets* (Gebru et al., 2021)
│   ├── figures/      # diagrams referenced by the docs
│   ├── findings.md   # main outcomes so far, one row per notebook, with caveats, candidate changes and open questions
│   └── working_rules.md  # the rules every analysis follows, each linked to its evidence
├── notebooks/        # EDA and analysis from Phase 01 onwards
├── src/              # reusable code: data loading, parsing, and later the ML/statistical methods
├── data/             # local data tiers: raw/, clean/, derived/
└── pyproject.toml
```

Storage is kept local for now. Datasets are Parquet files queried with DuckDB, which is ample for the current data volumes. Larger files may move to Azure or Git LFS later, but there is no need yet: the scripts in `src/` rebuild the datasets locally, so the raw files do not have to live in the repo to stay reproducible.

### Data layout

`data/` is split by transformation stage, not by dataset:

- **`raw/`** - immutable source dumps, never written by code (e.g. `fridge_lx2_grafana_20260903.csv`).
- **`clean/`** - minimally cleaned, lossless, analysis-ready. One subfolder per dataset (e.g. `clean/fridge/channels.parquet` + `channel_catalog.parquet`). A dataset spread over several stores is merged here (e.g. benchmarking's QCaaS payloads plus its server `output_info`).
- **`derived/`** - opinionated, lossy, modelling-ready artifacts built from `clean/`: masks, joins, features, aggregations.

Data flows one way: build scripts read `raw/` and write `clean/` (a build may look up ids in another dataset's clean index, but takes no values from it); analysis reads `clean/` and writes `derived/`. `raw/` is never mutated.

### Code layout

`src/qpu_drift/` mirrors the data flow, in four layers. Each layer imports only from the ones below it (analysis -> derived -> sources -> core):

- **`sources/`** - one package per data source (`measurements`, `hardware_models`, `benchmarking`, `fridge`), each turning `raw/` into `clean/` tables. `output_info.py` is the shared walk over the server `output_info` tree, used by benchmarking and the run map.
- **`derived/`** - tables that join sources: `run_map/` (which run wrote which parameter), `noise.py` (per-entity noise) and `supplement.py` (DS3 KPIs plus payload fits).
- **`analysis/`** - shared methods for the notebooks (`methods`, `series`, `jumps`, `constants`).
- **Core** - `paths.py` (data locations), `ids.py` (entity ids), `periods.py` (windows, breaks and incidents), `decode.py` (DS1 payload decoding) and `_io.py` (Parquet writes stamped with build metadata).

## Setup (uv)

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Sync the environment (creates `.venv`, installs deps plus the `notebook` and
   `dev` groups, and pins a compatible Python interpreter):

   ```bash
   uv sync
   ```

   `oqc-qcaas-client` requires Python `>=3.10,<3.13`, so uv selects a matching
   interpreter (downloading one if needed).

## Notebooks

Launch JupyterLab in the project environment:

```bash
uv run jupyter lab
```

Or register the environment as a named kernel for use elsewhere:

```bash
uv run python -m ipykernel install --user --name secondment
```

## Configuration/secrets

Auth tokens are read from environment variables, never hardcoded.

```bash
cp .env.example .env
# then edit .env and set OQC_AUTH_TOKEN
```

`.env` is gitignored. The scripts call `load_dotenv()` so the values are picked
up automatically. Required variables:

- `OQC_AUTH_TOKEN`: OQC Cloud authentication token
- `OQC_URL`: OQC Cloud base URL (optional, has a default)
- `OQC_QPU_ID`: target QPU id (optional, has a default)

## Running

Each build step is a console script (defined in `pyproject.toml`), listed here in a working order:

```bash
uv run build-measurements       # DS1 measurements index
uv run build-fridge             # DS4 fridge channels
uv run fetch-benchmarking       # DS3 pull from QCaaS (needs OQC_AUTH_TOKEN)
uv run build-benchmarking       # DS3 Level 1
uv run build-benchmarking-l2    # DS3 Level 2 plus the DS1 fetch manifest
uv run decode-ds1               # fill Level 2 rejects from the fetched DS1 payloads
uv run supplement-kpi           # optional: DS3 KPIs plus payload fits
uv run build-run-map            # run -> parameter map
```

DS2 is built once inside `.venv-maac` rather than with `uv run` (`build-hardware-models`, see datasheet 2). The datasheets give each script's inputs, options and the manual copies in between (the output_info folders and the DS1 payloads).

## Managing dependencies

```bash
uv add <package>                 # add a runtime dependency
uv add --group notebook <pkg>    # add to the notebook group
uv lock --upgrade                # refresh the lockfile
```