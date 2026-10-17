"""The sea rule: drop parcels with less than half their area on OpenStreetMap land.

io-lulc, which ``merge_polygons.py`` uses for inland water, has no data over open sea and counts
nodata as dry, so it cannot find offshore parcels (south-west Norway 2025: 3,361 offshore
parcels with a median ``frac_water`` of 0). The rule instead measures each parcel against the
OSM land polygons (osmdata.openstreetmap.de ``land-polygons-split-4326``, ODbL). Those are
derived from the coastline, so islands, polders and lakes count as land, while tidal flats
outside the coastline and the Caspian count as sea.

``SeaFilter`` wraps the Arrow batches ``fiboa_convert`` writes, and ``prep_land_polygons`` makes
the GeoParquet it reads (WKB geometry, bbox columns, sorted by xmin and ymin) from the
shapefile once::

    python pipeline/postprocessing/sea_filter.py land-polygons-split-4326/land_polygons.shp \\
        land_polygons.parquet

All geometries are lon/lat (EPSG:4326). Areas are measured in the parcel's UTM zone (north or
south by the parcel's bbox centre), like ``metrics:area``.
"""

import argparse
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely
from pyproj import Transformer

LAND_MIN = 0.5  # a parcel with less of its area on land is sea


def utm_transformers(zone: int) -> dict[bool, Transformer]:
    """{north?: lon/lat -> UTM zone transformer}."""
    return {
        True: Transformer.from_crs(4326, 32600 + zone, always_xy=True),
        False: Transformer.from_crs(4326, 32700 + zone, always_xy=True),
    }


def to_utm(geoms: np.ndarray, north: np.ndarray, trs: dict[bool, Transformer]) -> np.ndarray:
    """``geoms`` projected row by row into the north or south UTM CRS of ``trs``."""
    out = np.empty(len(geoms), dtype=object)
    for hemi, tr in trs.items():
        m = north == hemi
        if m.any():
            out[m] = shapely.transform(
                geoms[m], lambda xy, tr=tr: np.column_stack(tr.transform(xy[:, 0], xy[:, 1]))
            )
    return out


def north_of(geoms: np.ndarray) -> np.ndarray:
    b = shapely.bounds(geoms)
    return (b[:, 1] + b[:, 3]) / 2 >= 0


def land_fraction(
    geoms: np.ndarray, land: np.ndarray, land_tree: shapely.STRtree, zone: int
) -> np.ndarray:
    """Share of each parcel's area on land (0-1).

    ``land`` are OSM land polygons (``land-polygons-split-4326``: coastline-derived, lakes and
    islands are land; split into <= 1 degree pieces that overlap slightly). Parcels properly
    inside one piece are 1 without area work; parcels touching no piece are 0; the rest
    (coastal, or across a split line) get area(parcel n union of pieces) / area(parcel) in UTM.
    """
    n = len(geoms)
    frac = np.zeros(n)
    if n == 0 or len(land) == 0:
        return frac
    shapely.prepare(land)
    # each land piece prepared once, tested against the parcels inside its bbox
    _, inside = shapely.STRtree(geoms).query(land, predicate="contains_properly")
    frac[inside] = 1.0
    rest = np.flatnonzero(frac == 0)
    if rest.size == 0:
        return frac
    ri, li = land_tree.query(geoms[rest], predicate="intersects")
    if ri.size == 0:
        return frac
    order = np.argsort(ri, kind="stable")
    ri, li = ri[order], li[order]
    pieces = shapely.intersection(geoms[rest][ri], land[li])
    # pieces overlap a little along split lines: union them per parcel before measuring
    rows, start = np.unique(ri, return_index=True)
    ends = np.append(start[1:], ri.size)
    on_land = np.array(
        [
            pieces[s] if e - s == 1 else shapely.union_all(pieces[s:e])
            for s, e in zip(start, ends, strict=True)
        ],
        dtype=object,
    )
    idx = rest[rows]
    trs = utm_transformers(zone)
    north = north_of(geoms[idx])
    a_land = shapely.area(to_utm(on_land, north, trs))
    a_all = shapely.area(to_utm(geoms[idx], north, trs))
    frac[idx] = np.clip(np.divide(a_land, a_all, out=np.zeros_like(a_all), where=a_all > 0), 0, 1)
    return frac


def load_pieces(path: Path, cells: set[tuple[int, int]]) -> np.ndarray:
    """Features of ``path`` (WKB + bbox columns) whose bbox meets any of the 1-degree cells."""
    if not cells:
        return np.array([], dtype=object)
    xs, ys = zip(*cells, strict=True)
    flt = [("xmax", ">=", min(xs)), ("xmin", "<=", max(xs) + 1)]
    flt += [("ymax", ">=", min(ys)), ("ymin", "<=", max(ys) + 1)]
    t = pq.read_table(path, filters=flt)
    b = np.column_stack([t[k].to_numpy() for k in ("xmin", "ymin", "xmax", "ymax")])
    lo, hi = np.floor(b[:, :2]).astype(int), np.floor(b[:, 2:]).astype(int)
    keep = np.array(
        [
            any((x, y) in cells for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
            for x0, y0, x1, y1 in np.column_stack([lo, hi])
        ],
        bool,
    )
    return shapely.from_wkb(t["geometry"].filter(pa.array(keep)).to_numpy(zero_copy_only=False))


def occupied_cells(bbox: pa.Table) -> set[tuple[int, int]]:
    """1-degree cells (floor lon, floor lat) touched by rows with xmin..ymax columns."""
    lo = np.floor(np.column_stack([bbox["xmin"], bbox["ymin"]])).astype(int)
    hi = np.floor(np.column_stack([bbox["xmax"], bbox["ymax"]])).astype(int)
    cells: set[tuple[int, int]] = set()
    for x0, y0, x1, y1 in np.unique(np.column_stack([lo, hi]), axis=0):
        cells.update((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
    return cells


class SeaFilter:
    """Drops rows with less than ``land_min`` of their area on land from fiboa batches.

    Land pieces are loaded per 1-degree cell as batches reach them (rows arrive in Hilbert
    order, so a batch touches few cells) and kept for later batches.
    """

    def __init__(self, path: Path, zone: int, land_min: float = LAND_MIN) -> None:
        self.path, self.zone, self.land_min = path, zone, land_min
        self.cells: dict[tuple[int, int], np.ndarray] = {}
        self.dropped = 0

    def _pieces(self, cells: set[tuple[int, int]]) -> np.ndarray:
        new = cells - self.cells.keys()
        if new:
            got = load_pieces(self.path, new)
            b = shapely.bounds(got) if len(got) else np.zeros((0, 4))
            for c in new:  # a piece spanning several cells is listed under each
                m = (
                    (b[:, 0] <= c[0] + 1)
                    & (b[:, 2] >= c[0])
                    & (b[:, 1] <= c[1] + 1)
                    & (b[:, 3] >= c[1])
                )
                self.cells[c] = got[m]
        parts = [self.cells[c] for c in cells]
        # a piece listed under two cells is tested twice: harmless (unioned before measuring)
        return np.concatenate(parts) if parts else np.array([], dtype=object)

    def __call__(self, batches: Iterable[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        for b in batches:
            if b.num_rows == 0:
                continue
            bb = {k: pc.struct_field(b.column("bbox"), k) for k in ("xmin", "ymin", "xmax", "ymax")}
            land = self._pieces(occupied_cells(bb))
            geoms = shapely.from_wkb(b.column("geometry").to_numpy(zero_copy_only=False))
            frac = land_fraction(geoms, land, shapely.STRtree(land), self.zone)
            keep = frac >= self.land_min
            self.dropped += int((~keep).sum())
            yield b.filter(pa.array(keep))


def prep_land_polygons(shp: Path, dst: Path) -> int:
    """OSM land-polygons shapefile -> GeoParquet with bbox columns, for bbox-filtered reads."""
    import pyogrio

    _, _, wkb, _ = pyogrio.raw.read(shp)
    b = shapely.bounds(shapely.from_wkb(wkb))
    t = pa.table(
        {
            "geometry": pa.array(wkb, pa.binary()),
            **{k: b[:, i] for i, k in enumerate(("xmin", "ymin", "xmax", "ymax"))},
        }
    )
    t = t.take(pc.sort_indices(t, [("xmin", "ascending"), ("ymin", "ascending")]))
    pq.write_table(t, dst, row_group_size=4096, compression="zstd")
    return t.num_rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shapefile", type=Path, help="land-polygons-split-4326/land_polygons.shp")
    ap.add_argument("out", type=Path, help="land_polygons.parquet")
    a = ap.parse_args()
    print(f"{a.out}: {prep_land_polygons(a.shapefile, a.out):,} features")


if __name__ == "__main__":
    main()
