import numpy as np
import pytest

from qpu_drift.ids import coupling, coupling_key, edge, qubit


@pytest.mark.parametrize("x", [14, np.int64(14), "14", "Q14", "index=14"])
def test_qubit_reads_every_source_format(x):
    assert qubit(x) == 14


@pytest.mark.parametrize("x", ["", "Q", "q14", "C4-T5", "index=", 1.0])
def test_qubit_rejects_non_qubit_ids(x):
    with pytest.raises((ValueError, TypeError)):
        qubit(x)


def test_qubit_parses_bare_numbers():
    # DS1 readout labels like '00' would parse, so its tag scan filters on the Q prefix first
    assert qubit("00") == 0


@pytest.mark.parametrize(
    "x", ["C1-T10", "C1_T10", "1-10", "1_10", "direction=(1, 10)", "(1,10)", (1, 10), (np.int64(1), 10)],
)
def test_coupling_reads_every_source_format(x):
    assert coupling(x) == (1, 10)


def test_coupling_keeps_direction():
    assert coupling("C10-T1") == (10, 1)
    assert coupling("C1-T10") != coupling("C10-T1")


@pytest.mark.parametrize("x", ["", "Q1", "1", "C1-T", "1-10-2", "direction=(1)"])
def test_coupling_rejects_non_coupling_ids(x):
    with pytest.raises(ValueError):
        coupling(x)


def test_edge_pools_both_directions():
    assert edge("C10-T1") == edge("1-10") == (1, 10)


def test_coupling_key_is_control_dash_target():
    assert coupling_key("C4_T5") == coupling_key((4, 5)) == "4-5"
