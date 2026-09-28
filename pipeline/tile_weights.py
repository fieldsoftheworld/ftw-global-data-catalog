#!/usr/bin/env python3
"""Per-zoom tile-weight report for a PMTiles archive.

Prints count / mean / p50 / p99 / max stored (gzipped) tile bytes per zoom
plus the largest tiles overall — the check for the "cells tiles stay under
600 KB from z2 up" budget.
"""
import sys
from collections import defaultdict

from pmtiles.reader import Reader, MmapSource, all_tiles


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


path = sys.argv[1]
with open(path, "rb") as f:
    reader = Reader(MmapSource(f))
    by_zoom = defaultdict(list)
    biggest = []
    for (z, x, y), data in all_tiles(reader.get_bytes):
        n = len(data)
        by_zoom[z].append(n)
        biggest.append((n, z, x, y))

print(f"{'z':>2} {'tiles':>8} {'mean':>9} {'p50':>9} {'p99':>9} {'max':>10}")
for z in sorted(by_zoom):
    v = sorted(by_zoom[z])
    print(f"{z:>2} {len(v):>8} {sum(v)/len(v):>9,.0f} {pct(v, .5):>9,} "
          f"{pct(v, .99):>9,} {v[-1]:>10,}")

print("\nlargest tiles:")
for n, z, x, y in sorted(biggest, reverse=True)[:10]:
    print(f"  z{z} {x}/{y}: {n:,} bytes")
