import operator
import re

DEFAULT_QPU_ID = "qpu:jp:3:673b1ad43c"  # the device all four sources describe

_QUBIT = re.compile(r"(?:Q|index=)?(\d+)")                            # 'Q1' / '1' / 'index=1'
_COUPLING = re.compile(r"C?(\d+)[-_]T?(\d+)|(?:direction=)?\((\d+),\s*(\d+)\)")  # 'C1-T10' / 'C1_T10' / '1-10' / 'direction=(1, 10)'


def qubit(x) -> int:
    """Parse a qubit id in any source's format to its physical index.

    Accepts 1, '1', 'Q1' (DS1, output_info) and 'index=1' (DS2); raises ValueError otherwise.
    """
    if isinstance(x, str):
        m = _QUBIT.fullmatch(x)
        if m is None:
            raise ValueError(f"not a qubit id: {x!r}")
        return int(m.group(1))
    return operator.index(x)


def coupling(x) -> tuple[int, int]:
    """Parse a directed coupling id in any source's format to (control, target).

    Accepts 'C1-T10' (DS1), 'C1_T10' (output_info), '1-10' (DS3), 'direction=(1, 10)' (DS2)
    or a pair. Direction is kept; raises ValueError otherwise.
    """
    if isinstance(x, str):
        m = _COUPLING.fullmatch(x)
        if m is None:
            raise ValueError(f"not a coupling id: {x!r}")
        control, target = (int(g) for g in m.groups() if g is not None)
        return control, target
    control, target = x
    return operator.index(control), operator.index(target)


def coupling_key(x) -> str:
    """Return a coupling id in the canonical 'control-target' form (DS3 element, run_map entity)."""
    control, target = coupling(x)
    return f"{control}-{target}"


def edge(x) -> tuple[int, int]:
    """Return a coupling's undirected edge as a sorted pair, for pooling both directions."""
    return tuple(sorted(coupling(x)))
