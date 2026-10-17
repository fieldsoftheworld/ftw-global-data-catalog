"""Lossless codec for BoundaryVote outline tiles: WKB lon/lat <-> int32 deltas on the 2.5 m UTM grid.

Outlines live on the 2.5 m UTM grid: every vertex is a pixel corner, so the geometry is stored as
grid integers and rebuilt through the inverse projection. Decoding gives coordinates equal to the
original to float precision (the PROJ inverse can differ in the last bit between machines).
Callers verify the stored integers against the grid coordinates of the original.
"""

from functools import cache

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import shapely
from pyproj import Transformer

RES = 2.5
POLYGON, MULTIPOLYGON = 3, 6
CODEC_COLUMNS = ["is_multi", "x0", "y0", "dx", "dy", "ring_n", "part_n"]
INT32_MAX = 2**31 - 1
GRID_TOL = 0.01  # largest accepted distance of a vertex from the grid, in cells


def utm_epsg(tile_key):
    zone = int(tile_key[:2])
    if not 1 <= zone <= 60:
        raise ValueError(f"bad UTM zone in tile key {tile_key}")
    return (32700 if tile_key[2] < "N" else 32600) + zone


@cache
def _transformer(src, dst):
    return Transformer.from_crs(src, dst, always_xy=True)


def _list(offsets, values):
    return pa.ListArray.from_arrays(
        pa.array(offsets.astype(np.int32)), pa.array(values.astype(np.int32))
    )


def _int32(a):
    if np.abs(a).max(initial=0) > INT32_MAX:
        raise ValueError("coordinate does not fit int32")
    return a.astype(np.int32)


def encode(geoms, epsg):
    """Shapely polygons/multipolygons -> dict of arrow arrays named by CODEC_COLUMNS."""
    kinds = shapely.get_type_id(geoms)
    if not np.isin(kinds, (POLYGON, MULTIPOLYGON)).all() or shapely.is_empty(geoms).any():
        raise ValueError("non-polygon or empty geometry")
    _, coords, offsets = shapely.to_ragged_array(geoms, include_z=False)
    if len(offsets) == 2:
        ring_off, geom_as_part = offsets
        part_off, geom_off = geom_as_part, np.arange(len(geoms) + 1)
    else:
        ring_off, part_off, geom_off = offsets
    x, y = _transformer(4326, epsg).transform(coords[:, 0], coords[:, 1])
    qx, qy = np.rint(x / RES).astype(np.int64), np.rint(y / RES).astype(np.int64)
    if max(np.abs(x / RES - qx).max(initial=0), np.abs(y / RES - qy).max(initial=0)) > GRID_TOL:
        raise ValueError("vertex off the 2.5 m grid")
    bounds = ring_off[part_off[geom_off]]
    keep = np.ones(len(qx) - 1, dtype=bool)
    keep[bounds[1:-1] - 1] = False
    delta_off = np.concatenate([[0], np.cumsum(np.diff(bounds) - 1)])
    return {
        "is_multi": pa.array(kinds == MULTIPOLYGON),
        "x0": pa.array(_int32(qx[bounds[:-1]])),
        "y0": pa.array(_int32(qy[bounds[:-1]])),
        "dx": _list(delta_off, _int32(np.diff(qx)[keep])),
        "dy": _list(delta_off, _int32(np.diff(qy)[keep])),
        "ring_n": _list(part_off[geom_off], np.diff(ring_off)),
        "part_n": _list(geom_off, np.diff(part_off)),
    }


def _flat(col):
    arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    return pc.list_value_length(arr).to_numpy(), arr.flatten().to_numpy()


def _cumulative(counts):
    return np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)


def decode(cols, epsg):
    """Arrow columns named by CODEC_COLUMNS -> object array of shapely geometries."""
    is_multi = np.asarray(cols["is_multi"].to_numpy(zero_copy_only=False))
    x0, y0 = (
        np.asarray(cols[k].to_numpy(zero_copy_only=False)).astype(np.int64) for k in ("x0", "y0")
    )
    dlen, dx = _flat(cols["dx"])
    _, dy = _flat(cols["dy"])
    _, ring_n = _flat(cols["ring_n"])
    parts_per_row, part_n = _flat(cols["part_n"])
    n = dlen + 1
    starts = _cumulative(n)[:-1]
    is_start = np.zeros(int(n.sum()), dtype=bool)
    is_start[starts] = True
    q = []
    for first, delta in ((x0, dx), (y0, dy)):
        step = np.empty(len(is_start), dtype=np.int64)
        step[is_start], step[~is_start] = first, delta
        run = np.cumsum(step)
        base = np.where(starts > 0, run[np.maximum(starts - 1, 0)], 0)
        q.append(run - np.repeat(base, n))
    lon, lat = _transformer(epsg, 4326).transform(q[0] * RES, q[1] * RES)
    offsets = (_cumulative(ring_n), _cumulative(part_n), _cumulative(parts_per_row))
    geoms = shapely.from_ragged_array(
        shapely.GeometryType.MULTIPOLYGON, np.column_stack([lon, lat]), offsets
    )
    single = ~is_multi
    geoms[single] = shapely.get_geometry(geoms[single], 0)
    return geoms
