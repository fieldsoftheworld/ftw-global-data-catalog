"Report every worker failure and exit nonzero."

import sys
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, as_completed
from concurrent.futures.process import BrokenProcessPool


def drain(
    futs: dict[Future, str],
    on_result: Callable[[str, object], None],
) -> list[tuple[str, str]]:
    """Run on_result for each success; return [(key, traceback)] for failures after all finish.

    ``BrokenProcessPool`` is re-raised rather than recorded. It subclasses
    RuntimeError, so a bare ``except Exception`` swallowed it, and one killed
    worker breaks the pool for every task that follows: measured, a single
    self-SIGKILLed task out of 40 produced 0 successes and 40 identical
    BrokenProcessPool failures, none of which named the culprit. At 10k tiles that
    is 10k useless tracebacks hiding one OOM.
    """
    failures = []
    pending = dict(futs)
    for f in as_completed(futs):
        key = pending.pop(f, futs[f])
        try:
            res = f.result()
        except BrokenProcessPool:
            left = sorted(pending.values())
            head = ", ".join(left[:20]) + (" ..." if len(left) > 20 else "")
            print(
                f"  POOL BROKEN at {key}: a worker died without raising (an OOM kill "
                f"looks like this). {len(left)} task(s) still in flight: {head or 'none'}. "
                "Aborting the drain; nothing after this point would have run.",
                file=sys.stderr,
                flush=True,
            )
            raise
        except Exception as exc:
            tb = "".join(traceback.format_exception(exc))
            failures.append((key, tb))
            print(f"  FAILED {key}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            continue
        on_result(key, res)
    return failures


def retry_io_failures(
    failures: list[tuple[str, str]],
    submit: Callable[[str], Future],
    on_result: Callable[[str, object], None],
    wait_s: float = 60.0,
) -> list[tuple[str, str]]:
    """Resubmit, once and after ``wait_s``, the tasks that failed with a RasterioIOError.

    Remote reads (DEM, land cover, EODATA) see transient transport errors that
    outlast GDAL's own retries; a second pass in a fresh worker (no stale GDAL
    cache) recovers them instead of failing the job. Other errors are not retried.
    Returns the failures that remain.
    """
    # the exception line, not the whole traceback: quoted source lines can name the type
    io = [k for k, tb in failures if "RasterioIOError" in tb.rstrip().splitlines()[-1]]
    if not io:
        return failures
    print(f"retrying {len(io)} task(s) after I/O errors", file=sys.stderr, flush=True)
    time.sleep(wait_s)
    again = drain({submit(k): k for k in io}, on_result)
    return [f for f in failures if f[0] not in io] + again


def exit_on_failures(failures: list[tuple[str, str]], total: int, label: str = "tasks") -> None:
    "Print full tracebacks once and exit 1 so SLURM afterok chains stop."
    if not failures:
        return
    for key, tb in failures:
        print(f"--- {key}\n{tb}", file=sys.stderr, flush=True)
    keys = ", ".join(k for k, _ in failures[:10]) + (" ..." if len(failures) > 10 else "")
    print(f"{len(failures)}/{total} {label} failed: {keys}", file=sys.stderr, flush=True)
    sys.exit(1)
