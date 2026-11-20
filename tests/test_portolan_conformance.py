#!/usr/bin/env python3
"""Portolan conformance gate, via rashid.

Fails on any error-severity finding whose rule is not in ACCEPTED.

ACCEPTED ships empty, and the rule for growing it is not negotiable: every
entry needs a row in docs/conformance.md giving the rule, where it fires, why
it is accepted, and the issue tracking its removal. A known deviation with an
issue number is a debt. A silently widened allow-list is a lie about what this
catalog conforms to.

Runs with --no-data. The byte checks read every asset, which over remote hrefs
makes CI slow and dependent on a third-party host being up. Run the full check
yourself before publishing:

    rashid check catalog/

Fails when rashid is absent, or when its version is outside the required range.
A skip reports a green run for a catalog that no validator read. The failure
message gives the one command that installs a usable rashid.

Run: python3 tests/test_portolan_conformance.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

# Empty, and that is the goal state. PTL-VIZ-002 was the one entry and it is
# gone: under Portolan v0.3.0 the per-year `overview.tif` declares the COG
# media type it always should have, and rashid 0.4.0 no longer reads a
# `visual` COG as a derivative that needs a MapLibre style. See "the raster
# browse overview has no style document (closed)" in docs/conformance.md.
ACCEPTED: set[str] = set()

config = load_config()
target = ROOT / config["publish_dir"]

# This catalog declares Portolan v0.3.0, and 0.4.0 is the first rashid that
# bundles the v0.3.0 profile. An older one applies the v0.2 rule set, which
# misses the rules this catalog was migrated to meet (the one-file data asset,
# the listable `partition:glob`) and reports a v0.3.0 object as unknown. The
# upper bound stops an unreviewed 0.5 rule set from changing what this gate
# means: raise both bounds together, and read the new rules first.
MIN_VERSION = (0, 4, 0)
MAX_VERSION = (0, 5, 0)
SPEC = "rashid>=0.4.0,<0.5.0"
INSTALL = f"python -m pip install '{SPEC}'"


def fail(message: str) -> None:
    """Report the problem, name the fix, and exit non-zero."""
    print(f"error  {message}")
    print(f"       install a usable rashid with: {INSTALL}")
    raise SystemExit(1)


def rashid_version() -> tuple[int, ...]:
    """The version that rashid reports, as a tuple of integers."""
    try:
        proc = subprocess.run(
            ["rashid", "--version"], capture_output=True, text=True
        )
    except OSError as exc:
        fail(f"rashid --version did not run ({exc})")
    if proc.returncode != 0:
        fail(f"rashid --version exited {proc.returncode}")
    # rashid 0.1.6 prints "rashid, version 0.1.6". Read the first X.Y.Z in the
    # output, and fail on a string with no version in it. A gate that cannot
    # read the version must not assume the version is good.
    text = (proc.stdout + proc.stderr).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        fail(f"rashid --version printed no readable version: {text!r}")
    return tuple(int(part) for part in match.groups())


# The raster item tree (zone=/gzd= catalogs + 67k items) is generated and
# gitignored, not committed: a checkout without it would hand rashid a
# catalog whose year collections point at children that are not on disk —
# hundreds of findings about files that exist in the published bucket.
# Validating a half-tree proves nothing, so the gate refuses it instead:
# in CI (CI_LIGHT=1) that is a documented skip — the full conformance run
# happens pre-publish via pipeline/rashid_check.sbatch — and locally it
# says how to regenerate. See "generated item tree" in docs/conformance.md
# and portolan-sdi/rashid (bucket-resident item trees) linked there.
if not list((target / "raster").glob("*/zone=*/catalog.json")):
    message = (
        "the generated raster item tree is absent "
        "(catalog/raster/*/zone=*/); regenerate it with: "
        "python3 tools/build_raster_items.py items"
    )
    if os.environ.get("CI_LIGHT") == "1":
        print(f"note   {message}")
        print("note   conformance of the full tree runs pre-publish "
              "(pipeline/rashid_check.sbatch); nothing validated here")
        raise SystemExit(0)
    fail(message)

if shutil.which("rashid") is None:
    fail("rashid is not installed, so this gate checks nothing")

version = rashid_version()
shown = ".".join(str(part) for part in version)
if not MIN_VERSION <= version < MAX_VERSION:
    fail(f"rashid {shown} is outside the required range {SPEC}")

result = subprocess.run(
    ["rashid", "check", str(target), "--no-data", "--json"],
    capture_output=True,
    text=True,
)

try:
    report = json.loads(result.stdout)
except json.JSONDecodeError:
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    raise SystemExit("rashid produced no JSON report")

findings = report.get("findings", [])
blocking = [
    f for f in findings
    if f.get("severity") == "error" and f.get("rule_id") not in ACCEPTED
]

for finding in blocking:
    where = finding.get("path", "?")
    print(f"error  {finding.get('rule_id')}  {where}: {finding.get('message')}")
    if finding.get("fix_hint"):
        print(f"       hint: {finding['fix_hint']}")

waived = [f for f in findings if f.get("rule_id") in ACCEPTED]
if waived:
    print(f"\n{len(waived)} accepted finding(s); see docs/conformance.md")

if blocking:
    raise SystemExit(1)
print(
    f"OK: rashid {shown} found no blocking errors in {config['publish_dir']}/"
)
