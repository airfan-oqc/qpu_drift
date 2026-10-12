import pandas as pd
import pytest

from qpu_drift.derived.noise import COLUMNS, load_noise_floor, write_noise_rows


def _rows(source, sigma):
    t = pd.Timestamp("2026-01-01")
    return pd.DataFrame([[source, "p", "e", "r", t, t, "m", sigma, "K", 10]], columns=COLUMNS)


def test_write_replaces_only_own_source(tmp_path):
    path = tmp_path / "noise.parquet"
    assert load_noise_floor(path).empty
    write_noise_rows(_rows("ds4", 1.0), "ds4", path)
    write_noise_rows(_rows("ds2", 2.0), "ds2", path)
    out = write_noise_rows(_rows("ds4", 3.0), "ds4", path)
    assert sorted(out["source"]) == ["ds2", "ds4"]
    assert out.set_index("source")["sigma"].to_dict() == {"ds2": 2.0, "ds4": 3.0}
    assert load_noise_floor(path).equals(out)


def test_write_rejects_wrong_source_and_missing_columns(tmp_path):
    with pytest.raises(ValueError):
        write_noise_rows(_rows("ds2", 1.0), "ds4", tmp_path / "n.parquet")
    with pytest.raises(ValueError):
        write_noise_rows(_rows("ds4", 1.0).drop(columns="unit"), "ds4", tmp_path / "n.parquet")
