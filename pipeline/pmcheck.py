"""Local check of one year's handover PMTiles before upload.

Header zoom range 0-13, layers (cells z0-8, fields z9-13), tile counts per zoom against a
reference merge report (the release build of the same year), and z13 sample tiles whose field
counts are compared with the zone parquet bbox counts.
Usage: python pmcheck.py YEAR ARCHIVE SRC_ROOT REPORT [REF_REPORT]
"""

import gzip
import json
import math
import sys

import duckdb
import mapbox_vector_tile as mvt
from pmtiles.reader import MmapSource, Reader

year, path, src, report = sys.argv[1:5]
ref = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else None
rep = json.load(open(report))
bad = []


def tile(lon, lat, z):
    n = 2**z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def bounds(x, y, z):
    n = 2**z

    def f(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, f(y + 1), (x + 1) / n * 360 - 180, f(y)


c = duckdb.connect()
with open(path, "rb") as fh:
    r = Reader(MmapSource(fh))
    h = r.header()
    print(f"{year}: zoom {h['min_zoom']}-{h['max_zoom']}, tiles {h['tile_entries_count']:,}, addressed {h['addressed_tiles_count']:,}")
    if (h["min_zoom"], h["max_zoom"]) != (0, 13):
        bad.append("zoom range")
    meta = r.metadata()
    layers = {v["id"]: (v.get("minzoom"), v.get("maxzoom")) for v in meta.get("vector_layers", [])}
    print("layers", layers)
    if set(layers) != {"cells", "fields"}:
        bad.append(f"layers {layers}")
    for z, (name, want) in {4: ("z4", "cells"), 8: ("z8", "cells"), 9: ("z9", "fields"), 13: ("z13", "fields")}.items():
        x, y = tile(-93.6, 42.0, z)
        d = r.get(z, x, y)
        got = list(mvt.decode(gzip.decompress(d))) if d else None
        print(f"  iowa {name}: layers {got}")
        if not got or want not in got:
            bad.append(f"iowa {name} {got}")
    for name, lon, lat in [("iowa", -93.6, 42.0), ("kagera", 31.2, -1.5), ("pampas", -62.0, -34.0),
                           ("sask", -106.0, 52.0), ("punjab", 75.0, 31.0), ("jaeren", 5.65, 58.75),
                           ("nl_6e", 6.02, 52.6), ("thai_102e", 102.2, 15.0)]:
        z = 13
        x, y = tile(lon, lat, z)
        d = r.get(z, x, y)
        fs = mvt.decode(gzip.decompress(d)).get("fields", {}).get("features", []) if d else []
        w, s, e, n = bounds(x, y, z)
        k = c.execute(
            f"select count(*) from read_parquet('{src}/{year}/zone=*/utm*.parquet') where bbox.xmin<={e} and bbox.xmax>={w} and bbox.ymin<={n} and bbox.ymax>={s}"
        ).fetchone()[0]
        print(f"  {name} z13: tile features {len(fs)} | parquet in bbox {k}")
        if k > 0 and len(fs) == 0:
            bad.append(f"{name} empty tile, {k} parcels")
counts = rep["per_zoom_tile_counts"]
print("per zoom", counts)
if ref:
    for z, v in ref["per_zoom_tile_counts"].items():
        nv = counts.get(z, 0)
        if abs(nv / v - 1) > 0.15:
            bad.append(f"z{z} tiles {nv} vs ref {v}")
    print(f"total tiles {rep['tiles_total']:,} vs ref {ref['tiles_total']:,}; bytes {rep['bytes_written'] / 1e9:.2f} GB vs {ref['bytes_written'] / 1e9:.2f} GB")
print("PMCHECK", "FAIL " + "; ".join(bad) if bad else "OK")
sys.exit(1 if bad else 0)
