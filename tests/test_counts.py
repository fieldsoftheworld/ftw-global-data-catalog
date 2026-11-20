#!/usr/bin/env python3
"""The catalog's own numbers agree with each other.

Every parcel count is stamped prose. The item's `table:row_count` is the
measurement; it is then repeated in the year collection's description, the
year README, the two summary tables, and — summed over the nine years — in
the repo-level READMEs. Copies of one number that no other gate reads back,
so a rebuild that refreshes some and not the others publishes a catalog that
contradicts itself. This gate reads them back. It is the gate that caught the
published tables disagreeing with the published collections for 2018, 2023,
2024 and 2025 (corrected by tools/apply_v03_metadata.py, which records why).

Two shapes of document are in the tree, because `catalog/` mirrors the
published catalog and the seven re-generated years were written by the
pipeline in taylor-geospatial/global-ftw-2e rather than by this repository's
generators. They state different sets of facts: this repository's year README
leads with "**N parcels** in K per-UTM-zone GeoParquet files (G GiB)", the
other with "N parcels in K UTM-zone files". So each number is checked **where
it appears**: a document that states a count must state the right one, a
document that does not state it is not required to. The checks that are
unconditional are the ones every shape carries — the collection description,
the year README's leading count, and the repo-level grand totals — and the
gate prints how many conditional checks actually ran, so a document quietly
losing its numbers shows up as that count falling.

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
    """The parcel count in that year's summary-table row, if it states one.

    The table is `| [year](link) | Processing | Raster tiles | Parcels |`.
    A year the publisher did not reprocess carries "See collection" in the
    last two cells instead of numbers; that is a statement, not a gap, so it
    is allowed through.
    """
    match = re.search(rf"^\| \[{year}\][^|]*\|[^|]*\|([^|]*)\|([^|]*)\|$",
                      text, re.M)
    if match is None:
        check(False, f"{where}: no table row for {year}")
        return None
    cell = match.group(2).strip()
    if cell == "See collection":
        return None
    if not re.fullmatch(r"[\d,]+", cell):
        check(False, f"{where} row {year}: {cell!r} is not a parcel count")
        return None
    return int(cell.replace(",", ""))


years = sorted(int(p.name) for p in VECTOR.iterdir()
               if p.is_dir() and re.fullmatch(r"\d{4}", p.name))
check(len(years) >= 1, "no year directories under catalog/vector")

vector_readme = (VECTOR / "README.md").read_text()
root_readme = (PUBLISH_DIR / "README.md").read_text()

conditional = 0


def agrees(text: str, pattern: str, expected: int, where: str) -> None:
    """A number stated in `text` must be `expected`. Silence is allowed.

    Only the first match is read. A document that does not state the number
    is not required to; one that states it twice has to agree twice.
    """
    global conditional
    for match in re.finditer(pattern, text, re.M):
        conditional += 1
        stated = int(match.group(1).replace(",", ""))
        if stated != expected:
            errors.append(f"{where}: {stated:,} != {expected:,}")


total = 0
for year in years:
    where = f"catalog/vector/{year}"
    year_dir = VECTOR / str(year)
    # Both item layouts: this repository writes `zone=NN/utmNN.json`, the
    # published tree's re-generated years use `utmNN/utmNN.json`.
    items = sorted(year_dir.glob("*/utm*.json"))
    check(bool(items), f"{year}: no item JSON under {where}")
    if not items:
        continue
    docs = [json.loads(p.read_text()) for p in items]
    counts = [d["properties"]["table:row_count"] for d in docs]
    n = sum(counts)
    total += n
    gib = f"{sum(d['assets']['data']['file:size'] for d in docs) / 2**30:,.1f}"

    for path, doc, count in zip(items, docs, counts):
        agrees(doc["properties"].get("description", ""),
               r"([\d,]+) parcels", count,
               f"{path.relative_to(ROOT)} properties.description")
        agrees(doc["assets"]["data"].get("description", ""),
               r"([\d,]+) predicted field parcels", count,
               f"{path.relative_to(ROOT)} data asset description")

    description = json.loads(
        (year_dir / "collection.json").read_text())["description"]
    check(f"{n:,} parcels" in description,
          f"{where}/collection.json: description is not {n:,} parcels")
    check(f"{gib} GiB" in description,
          f"{where}/collection.json: description is not {gib} GiB")

    year_readme = (year_dir / "README.md").read_text()
    leading = re.search(r"([\d,]+) parcels", year_readme)
    check(leading is not None,
          f"{where}/README.md: states no parcel count")
    if leading and int(leading.group(1).replace(",", "")) != n:
        errors.append(f"{where}/README.md: "
                      f"{leading.group(1)} != {n:,}")
    biggest = max(counts)
    agrees(year_readme, r"with ([\d,]+) parcels", biggest,
           f"{where}/README.md largest zone")
    agrees((year_dir / "AGENTS.md").read_text(), r"- ([\d,]+) parcels", n,
           f"{where}/AGENTS.md")

    for name, text in (("catalog/vector/README.md", vector_readme),
                       ("catalog/README.md", root_readme)):
        stated = row(text, year, name)
        if stated is not None and stated != n:
            errors.append(f"{name} row {year}: {stated:,} != {n:,}")

# The grand total. The first three state it only in this repository's own
# prose, which the published tree replaced for the re-generated years, so
# they are checked where they appear. The last two are this repository's
# README and pipeline README, which always state it.
for name, text, pattern in [
    ("catalog/README.md", root_readme,
     r"\*\*([\d,]+) predicted field polygons\*\*"),
    ("catalog/vector/README.md", vector_readme,
     r"\*\*([\d,]+) parcels\*\* total"),
    ("catalog/catalog.json",
     json.loads((PUBLISH_DIR / "catalog.json").read_text())["description"],
     r"([\d,]+) predicted field polygons"),
]:
    agrees(text, pattern, total, name)

for name, pattern in [
    ("README.md", r"([\d,]+) predicted agricultural field"),
    ("pipeline/README.md", r"([\d,]+) field polygons"),
]:
    text = (ROOT / name).read_text()
    stated = found(pattern, text, name)
    if stated is not None and stated != total:
        errors.append(f"{name}: total {stated:,} != {total:,}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print(f"OK: {len(years)} years, {total:,} parcels; every stated count and GiB "
      f"agrees with the items ({conditional} conditional check(s) ran)")
