#!/usr/bin/env python3
"""The catalog's own numbers agree with each other.

Every parcel count here is stamped prose. The zone item's `table:row_count` is
the measurement; it is then repeated in that item's own description, in the
year collection's description, in the year README and AGENTS.md, in the vector
tree README's table, in the root README's table, and — summed over the nine
years — in the root catalog's description and two repo-level READMEs. Seven
copies of one number, none of them read back by any other gate, so a rebuild
that refreshes some and not the others publishes a catalog that contradicts
itself. This gate reads them back.

Per year: the item descriptions, the collection description, the year README
and AGENTS.md, the "largest zone" sentence and both tables must agree with the
items; the stated GiB must agree with the sum of the items' `file:size`. Then
the grand total in the root README, the root catalog description and the repo's
README.md and pipeline/README.md must equal the sum of the nine years.

Run: python3 tests/test_counts.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from publish import load_config  # noqa: E402

PUBLISH_DIR = ROOT / load_config()["publish_dir"]
VECTOR = PUBLISH_DIR / "vector"

errors = []


def check(ok: bool, message: str) -> None:
    if not ok:
        errors.append(message)


def found(pattern: str, text: str, where: str) -> int | None:
    """The one integer `pattern` captures in `text`, else None and an error."""
    hits = {m.group(1) for m in re.finditer(pattern, text, re.M)}
    if len(hits) != 1:
        check(False, f"{where}: expected one match for {pattern!r}, "
                     f"found {sorted(hits) or 'none'}")
        return None
    return int(hits.pop().replace(",", ""))


def row(text: str, year: int, where: str) -> int | None:
    """The parcel count in the `| {year} | {count} |` table row."""
    return found(rf"^\| {year} \| ([\d,]+) \|", text, f"{where} row {year}")


years = sorted(int(p.name) for p in VECTOR.iterdir()
               if p.is_dir() and re.fullmatch(r"\d{4}", p.name))
check(len(years) >= 1, "no year directories under catalog/vector")

vector_readme = (VECTOR / "README.md").read_text()
root_readme = (PUBLISH_DIR / "README.md").read_text()

total = 0
for year in years:
    where = f"catalog/vector/{year}"
    year_dir = VECTOR / str(year)
    items = sorted(year_dir.glob("zone=*/*.json"))
    check(bool(items), f"{year}: no zone items")
    if not items:
        continue
    docs = [json.loads(p.read_text()) for p in items]
    counts = [d["properties"]["table:row_count"] for d in docs]
    n = sum(counts)
    total += n
    gib = f"{sum(d['assets']['data']['file:size'] for d in docs) / 2**30:,.1f}"

    for path, doc, count in zip(items, docs, counts):
        check(f"{count:,} parcels" in doc["properties"]["description"],
              f"{path.relative_to(ROOT)}: description disagrees with "
              f"table:row_count {count:,}")

    description = json.loads(
        (year_dir / "collection.json").read_text())["description"]
    check(f"{n:,} parcels" in description,
          f"{where}/collection.json: description is not {n:,} parcels")
    check(f"{gib} GiB" in description,
          f"{where}/collection.json: description is not {gib} GiB")

    year_readme = (year_dir / "README.md").read_text()
    check(f"**{n:,} parcels**" in year_readme,
          f"{where}/README.md: headline is not {n:,} parcels")
    check(f"({gib} GiB)" in year_readme,
          f"{where}/README.md: headline is not {gib} GiB")
    biggest = max(counts)
    check(f"with {biggest:,} parcels" in year_readme,
          f"{where}/README.md: largest zone is not {biggest:,} parcels")

    agents = (year_dir / "AGENTS.md").read_text()
    check(f"- {n:,} parcels" in agents,
          f"{where}/AGENTS.md: first bullet is not {n:,} parcels")

    for name, text in (("catalog/vector/README.md", vector_readme),
                       ("catalog/README.md", root_readme)):
        stated = row(text, year, name)
        if stated is not None and stated != n:
            errors.append(f"{name} row {year}: {stated:,} != {n:,}")

# The grand total, in the five places that state it.
TOTALS = [
    ("catalog/README.md", root_readme,
     r"\*\*([\d,]+) predicted field polygons\*\*"),
    ("catalog/vector/README.md", vector_readme,
     r"\*\*([\d,]+) parcels\*\* total"),
    ("catalog/catalog.json",
     json.loads((PUBLISH_DIR / "catalog.json").read_text())["description"],
     r"([\d,]+) predicted field polygons"),
    ("README.md", (ROOT / "README.md").read_text(),
     r"([\d,]+) predicted agricultural field"),
    ("pipeline/README.md", (ROOT / "pipeline" / "README.md").read_text(),
     r"([\d,]+) field polygons"),
]
for name, text, pattern in TOTALS:
    stated = found(pattern, text, name)
    if stated is not None and stated != total:
        errors.append(f"{name}: total {stated:,} != {total:,}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: {len(years)} years, {total:,} parcels; every stated count and GiB "
      "agrees with the items")
