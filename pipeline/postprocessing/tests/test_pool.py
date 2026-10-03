"""A dead worker must abort the drain, not bury the pipeline in copies of itself."""

import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pool_utils
import simplify_polygons as sp

import outlines as ol


def _suicide(i: int) -> int:
    if i == 0:
        os.kill(os.getpid(), signal.SIGKILL)  # what the OOM killer does to a worker
    return i


def test_broken_pool_aborts_the_drain_instead_of_recording_every_task(capsys):
    """BrokenProcessPool subclasses RuntimeError, so `except Exception` ate it.

    Measured before the fix: one self-SIGKILLed task out of 40 gave 0 successes
    and 40 identical BrokenProcessPool failures, none naming the culprit.
    """
    with pytest.raises(BrokenProcessPool):
        with ProcessPoolExecutor(2, max_tasks_per_child=1, mp_context=ol.MP_CONTEXT) as pool:
            futs = {pool.submit(_suicide, i): f"tile{i:02d}" for i in range(40)}
            pool_utils.drain(futs, lambda k, r: None)
    err = capsys.readouterr().err
    assert "POOL BROKEN" in err
    assert "in flight" in err


def test_ordinary_worker_exceptions_are_still_collected(capsys):
    "Control: a task that raises normally must not abort the drain."

    def boom(x: int) -> int:
        if x % 3 == 0:
            raise ValueError(f"bad {x}")
        return x

    from concurrent.futures import ThreadPoolExecutor

    ok = []
    with ThreadPoolExecutor(2) as pool:
        futs = {pool.submit(boom, i): f"t{i}" for i in range(1, 8)}
        failures = pool_utils.drain(futs, lambda k, r: ok.append(k))
    assert len(ok) == 5
    assert sorted(k for k, _ in failures) == ["t3", "t6"]


def test_pool_context_is_explicit_and_spawn():
    """max_tasks_per_child silently switches fork -> spawn; say so in the code.

    Measured: ProcessPoolExecutor(1) gets ForkContext,
    ProcessPoolExecutor(1, max_tasks_per_child=1) gets SpawnContext.
    """
    for mod in (ol, sp):
        assert mod.MP_CONTEXT.get_start_method() == "spawn", mod.__name__
