"Report every worker failure and exit nonzero."

import sys
import traceback
from collections.abc import Callable
from concurrent.futures import Future, as_completed


def drain(
    futs: dict[Future, str],
    on_result: Callable[[str, object], None],
) -> list[tuple[str, str]]:
    "Run on_result for each success; return [(key, traceback)] for failures after all finish."
    failures = []
    for f in as_completed(futs):
        key = futs[f]
        try:
            res = f.result()
        except Exception as exc:
            tb = "".join(traceback.format_exception(exc))
            failures.append((key, tb))
            print(f"  FAILED {key}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            continue
        on_result(key, res)
    return failures


def exit_on_failures(failures: list[tuple[str, str]], total: int, label: str = "tasks") -> None:
    "Print full tracebacks once and exit 1 so SLURM afterok chains stop."
    if not failures:
        return
    for key, tb in failures:
        print(f"--- {key}\n{tb}", file=sys.stderr, flush=True)
    keys = ", ".join(k for k, _ in failures[:10]) + (" ..." if len(failures) > 10 else "")
    print(f"{len(failures)}/{total} {label} failed: {keys}", file=sys.stderr, flush=True)
    sys.exit(1)
