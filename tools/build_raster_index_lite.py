#!/usr/bin/env python3
"""Build ``index/raster-lite.parquet``, the slim copy of ``index/raster.parquet``.

Viewers only need to find which tiles cover the map, so the lite index keeps seven
columns and drops hrefs, sizes, per-tile statistics and the geometry::

    year      int16
    tile_key  string
    epsg      int32
    xmin, ymin, xmax, ymax   float32, WGS84 bbox, widened outward

Hrefs are not stored: they follow ``raster/{year}/zone={tile_key[:2]}/gzd={tile_key[:3]}/
{tile_key}/{tile_key}.tif`` and are rebuilt by the reader. The float32 bbox is rounded
outward (min down, max up) so a tile is never culled early. Rows are sorted by
(tile_key, year), ZSTD level 19, no column statistics: ~110 kB for the 67,197 tiles of
2017-2025, against ~1.9 MB for the full index.

    python3 tools/build_raster_index_lite.py [SRC] [DST]
    # defaults: staging-data/index/raster.parquet -> staging-data/index/raster-lite.parquet

SRC may also be an ``http(s)`` URL -- the published index at
``https://data.source.coop/ftw/global-data-2e/index/raster.parquet`` is the usual one -- which
is fetched into memory, since pyarrow has no filesystem for that scheme.

This only writes a local file. Uploading it is a separate, explicit step
(``tools/upload_data.py``).
"""
import io
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "staging-data" / "index" / "raster.parquet"
DST = ROOT / "staging-data" / "index" / "raster-lite.parquet"
KEEP = ["year", "tile_key", "epsg", "xmin", "ymin", "xmax", "ymax"]


def outward_f32(values: np.ndarray, up: bool) -> np.ndarray:
    """float32 of ``values``, nudged one ulp so the result is >= (up) or <= (down) the input."""
    f = values.astype(np.float32)
    wrong = (f < values) if up else (f > values)
    return np.where(wrong, np.nextafter(f, np.float32(np.inf if up else -np.inf)), f)


def build_lite(table: pa.Table) -> pa.Table:
    "The lite table for a raster index table (needs the seven KEEP columns)."
    t = table.select(KEEP).sort_by([("tile_key", "ascending"), ("year", "ascending")])
    cols = {
        "year": t["year"].cast(pa.int16()),
        "tile_key": t["tile_key"],
        "epsg": t["epsg"].cast(pa.int32()),
    }
    for c in ("xmin", "ymin", "xmax", "ymax"):
        cols[c] = pa.array(outward_f32(t[c].to_numpy(), up=c.endswith("max")))
    return pa.table(cols)


def write_lite(table: pa.Table, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dst, compression="zstd", compression_level=19, write_statistics=False)


def read_index(src: str | Path) -> pa.Table:
    "The KEEP columns of a raster index, from a local path or an http(s) URL."
    if str(src).startswith(("http://", "https://")):
        req = urllib.request.Request(str(src), headers={"User-Agent": "ftw-global-data-catalog"})
        with urllib.request.urlopen(req) as response:  # noqa: S310 - http(s) only, checked above
            src = io.BytesIO(response.read())
    return pq.read_table(src, columns=KEEP)


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else SRC
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else DST
    lite = build_lite(read_index(src))
    write_lite(lite, dst)
    print(f"{lite.num_rows:,} rows, {dst.stat().st_size / 1e3:.0f} kB -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
