"""Check a compacted outline year against its raw listing and write the manifest.

Every raw tile must have exactly one compact file (same stem), no extra compact files, equal tile
counts, and the list of tiles that produced no parcels is saved next to them as ``_empty.txt``.
The manifest maps each compact file to its size and sha256; ``--check`` re-hashes a copy (for
example one pulled from the bucket) against it.
Usage: compact_verify.py YEAR --raw-list RAW.txt --empty-list EMPTY.txt --compact-dir DIR --manifest OUT.json
       compact_verify.py YEAR --compact-dir DIR --manifest M.json --check   # re-hash a pulled copy against the manifest
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

CHUNK = 8 * 1024 * 1024


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def stems(names: list[str]) -> set[str]:
    return {Path(n.strip()).name.removesuffix(".parquet") for n in names if n.strip()}


def build_manifest(year: str, raw: set[str], empty: list[str], compact_dir: Path) -> dict:
    """Raise ValueError unless compact tiles == raw tiles; return the manifest."""
    files = {p.name.removesuffix(".parquet"): p for p in compact_dir.glob("*.parquet")}
    missing, extra = sorted(raw - files.keys()), sorted(files.keys() - raw)
    if missing or extra or len(files) != len(raw):
        raise ValueError(
            f"{year}: raw {len(raw)} vs compact {len(files)} tiles; missing {missing[:5]}, extra {extra[:5]}"
        )
    return {
        "year": int(year),
        "tiles": len(files),
        "empty": len(empty),
        "files": {
            n: {"size": p.stat().st_size, "sha256": sha256(p)} for n, p in sorted(files.items())
        },
    }


def check_manifest(manifest: dict, compact_dir: Path) -> list[str]:
    """Return problems of a local copy against the manifest (size first, then sha256)."""
    bad = []
    for name, want in manifest["files"].items():
        p = compact_dir / f"{name}.parquet"
        if not p.exists():
            bad.append(f"missing {name}")
        elif p.stat().st_size != want["size"] or sha256(p) != want["sha256"]:
            bad.append(f"differs {name}")
    extra = {p.stem for p in compact_dir.glob("*.parquet")} - manifest["files"].keys()
    bad += [f"extra {n}" for n in sorted(extra)]
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("year")
    ap.add_argument("--raw-list")
    ap.add_argument("--empty-list")
    ap.add_argument("--compact-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cdir = Path(a.compact_dir)
    if a.check:
        bad = check_manifest(json.loads(Path(a.manifest).read_text()), cdir)
        print(f"{a.year}: {len(bad)} problems", *bad[:20], sep="\n" if bad else "")
        sys.exit(1 if bad else 0)
    raw = stems(Path(a.raw_list).read_text().splitlines())
    empty = sorted(stems(Path(a.empty_list).read_text().splitlines())) if a.empty_list else []
    try:
        manifest = build_manifest(a.year, raw, empty, cdir)
    except ValueError as e:
        print(f"FAIL {e}")
        sys.exit(1)
    (cdir / "_empty.txt").write_text("".join(f"{n}\n" for n in empty))
    Path(a.manifest).write_text(json.dumps(manifest, indent=0))
    print(f"OK {a.year}: {manifest['tiles']} compact tiles == raw, {len(empty)} empty markers")


if __name__ == "__main__":
    main()
