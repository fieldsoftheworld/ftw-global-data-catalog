#!/usr/bin/env python3
"""The one place the 2017/2024 under-detection caveat is written.

Some regions are under-detected in the 2017 and 2024 predictions; a root-cause
investigation is running. The note is published in several places (root and tree
READMEs/AGENTS, the per-year docs, the 2017/2024 collection descriptions). To change
or retire it, edit ``TEXT`` / ``YEARS`` here and run

    python3 tools/known_limitation.py          # rewrite every managed location
    python3 tools/known_limitation.py --check  # exit 1 if any location is out of date

Markdown files carry the note between ``BEGIN`` and ``END`` marker comments; once the
investigation concludes, set ``TEXT = ""`` and run it to empty every block (the markers stay
so a later finding is one edit away). Collection descriptions get the note as a final bold
paragraph, and lose it again when ``TEXT`` is empty. ``tools/build_vector_items.py`` imports
``block`` and ``description_note`` so regenerating the vector tree keeps the note.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"

#: Years whose per-year docs and collection descriptions carry the note.
YEARS = (2017, 2024)
TEXT = (
    "Known limitation, under investigation: the 2017 and 2024 predictions are "
    "under-detected in some regions."
)
BEGIN = "<!-- known-limitation:begin -->"
END = "<!-- known-limitation:end -->"

#: Markdown files that always carry the (generic) block, relative to catalog/.
TREE_DOCS = (
    "README.md", "AGENTS.md",
    "raster/README.md", "raster/AGENTS.md",
    "vector/README.md", "vector/AGENTS.md",
)


def block() -> str:
    "The marker-wrapped note for markdown (markers stay when ``TEXT`` is empty)."
    inner = f"\n**{TEXT}**\n" if TEXT else "\n"
    return f"{BEGIN}{inner}{END}"


def description_note(year: int) -> str:
    "Final paragraph for a collection description, or '' when the year is not affected."
    return f"\n\n**{TEXT}**" if TEXT and year in YEARS else ""


def year_docs() -> list[Path]:
    return [
        CATALOG / tree / str(year) / name
        for tree in ("raster", "vector") for year in YEARS
        for name in ("README.md", "AGENTS.md")
    ]


def collections() -> list[Path]:
    return [CATALOG / tree / str(y) / "collection.json"
            for tree in ("raster", "vector") for y in YEARS]


def replace_block(text: str) -> str | None:
    "``text`` with its marker block refreshed, or None when it has no markers."
    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < i:
        return None
    return text[:i] + block() + text[j + len(END):]


def strip_note(description: str) -> str:
    return description.split("\n\n**Known limitation", 1)[0]


def refresh_description(path: Path) -> str:
    "The collection's description with the note applied (or removed) for its year."
    doc = json.loads(path.read_text())
    year = int(path.parent.name)
    return strip_note(doc["description"]) + description_note(year)


def stale() -> list[str]:
    out = []
    for rel in TREE_DOCS:
        path = CATALOG / rel
        new = replace_block(path.read_text())
        if new is None:
            out.append(f"{path.relative_to(ROOT)}: no {BEGIN} markers")
        elif new != path.read_text():
            out.append(f"{path.relative_to(ROOT)}: note out of date")
    for path in year_docs():
        new = replace_block(path.read_text())
        if new is None:
            out.append(f"{path.relative_to(ROOT)}: no {BEGIN} markers")
        elif new != path.read_text():
            out.append(f"{path.relative_to(ROOT)}: note out of date")
    for path in collections():
        if json.loads(path.read_text())["description"] != refresh_description(path):
            out.append(f"{path.relative_to(ROOT)}: description note out of date")
    for tree in ("raster", "vector"):  # unaffected years must not carry it
        for path in sorted((CATALOG / tree).glob("20??/collection.json")):
            if int(path.parent.name) not in YEARS and "Known limitation" in path.read_text():
                out.append(f"{path.relative_to(ROOT)}: carries the note but is not in YEARS")
    return out


def apply() -> None:
    for path in [CATALOG / r for r in TREE_DOCS] + year_docs():
        new = replace_block(path.read_text())
        if new is None:
            sys.exit(f"{path.relative_to(ROOT)}: add the {BEGIN} / {END} markers first")
        path.write_text(new)
    for path in collections():
        doc = json.loads(path.read_text())
        doc["description"] = refresh_description(path)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    if "--check" in sys.argv[1:]:
        problems = stale()
        print("\n".join(f"error  {p}" for p in problems) or "OK: known-limitation note in sync")
        return 1 if problems else 0
    apply()
    print("known-limitation note refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
