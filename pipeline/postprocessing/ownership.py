"""Which tile keeps a parcel: its UTM zone and 100 km MGRS square, plus orphan-strip claims.

A tile keeps a parcel whose centroid lies in its own 100 km square and in its UTM zone. Near zone
edges the mosaic product has no tile for some partial squares (32UK* in zone 32 between 6E and the
32UL* squares, 52-53N; 16TB* west of -89.4E at 42-43N). The zone that contains such a location has
no tile there, and the neighbouring zone's tile, whose square reaches across the zone edge, sees
the parcels but flags them outside its zone, so until Oct 2026 nobody kept them (3.0-3.4M parcels,
167-174k km2 a year in 2020-2025).

The rule: a tile also keeps (``claimed``) a parcel of its own square outside its zone when the
centroid, or any point of the centroid row across the parcel, lies in an adjacent zone's square
that has no tile in the run. Of two adjacent-zone claimers the western zone wins. A claimed field
that reaches into the next zone's first square overlaps that tile's own piece of it, and
``cross_zone_seams.py`` unions the two after merge.

The owned squares are the run's keep-list tiles. ``owned_squares_2e.csv`` is the 2nd Edition's
(7,467 tiles, the r1 run); ``owned_squares.csv`` is the current keep-list's (14,608 tiles).

    python ownership.py squares --keep-list keep.txt --footprints tile_footprints.parquet \\
        --out owned_squares.csv
"""

import argparse
import csv
import hashlib
import math
from functools import cache
from pathlib import Path

import numpy as np
from pyproj import Transformer

HERE = Path(__file__).resolve().parent
SQUARES = HERE / "owned_squares.csv"
BANDS = "CDEFGHJKLMNPQRSTUVWX"  # 8 degrees each from 80S (X is 12)
CLAIM_STEP_M = 200.0  # spacing of the claim test points along a parcel's centroid row


def lon_in_zone(zn: int, band: str, lon: float) -> bool:
    """Is ``lon`` inside UTM zone ``zn`` on the grid the Sentinel-2 tiles use?

    Band V (56-64N) is not on the nominal 6-degree pitch: 32V is widened to 3-12E
    and 31V narrowed to 0-3E, the convention MGRS and the Sentinel-2 tiling grid
    both follow. Testing 32V against the nominal 6-12E band rejects every parcel
    between 3E and 6E -- Bergen 5.32, Stavanger 5.73, Jaeren 5.60 -- and because
    no 31V tile covers 3-6E those parcels are lost outright rather than claimed by
    a neighbour. Band V lies well inside this catalog's 65.82N extent.

    Band X (72-84N) has its own, different exceptions (32X/34X/36X do not exist)
    and begins above that extent, so it is rejected rather than guessed at.
    """
    if band == "X":
        raise ValueError(f"zone {zn}{band}: band X UTM exceptions are not implemented")
    if band == "V" and zn in (31, 32):
        w, e = (0.0, 3.0) if zn == 31 else (3.0, 12.0)
    else:
        w, e = -180.0 + 6 * (zn - 1), -180.0 + 6 * zn
    return w <= lon < e


def band_of(lat: float) -> str:
    return BANDS[min(int((lat + 80) // 8), len(BANDS) - 1)] if -80 <= lat < 84 else "?"


def zone_of(lon: float, lat: float) -> int:
    """UTM zone of a location on the Sentinel-2 grid, with the band V exception."""
    if band_of(lat) == "V":
        for z in (31, 32):
            if lon_in_zone(z, "V", lon):
                return z
    return int((lon + 180) // 6) % 60 + 1


def neighbour(zone: int, step: int) -> int:
    return (zone - 1 + step) % 60 + 1


@cache
def _to_utm(zone: int, north: bool) -> Transformer:
    return Transformer.from_crs(4326, (32600 if north else 32700) + zone, always_xy=True)


def square_of(zone: int, lon: float, lat: float) -> tuple[int, bool, int, int]:
    """(zone, north, x0, y1) of the 100 km square of ``zone`` holding the location."""
    north = lat >= 0
    e, n = _to_utm(zone, north).transform(lon, lat)
    return zone, north, int(math.floor(e / 1e5) * 1e5), int(math.ceil(n / 1e5) * 1e5)


@cache
def load_squares(path: Path = SQUARES) -> frozenset:
    with Path(path).open() as f:
        return frozenset(
            (int(r["zone"]), r["north"] == "1", int(r["x0"]), int(r["y1"]))
            for r in csv.DictReader(f)
        )


@cache
def squares_digest(path: Path = SQUARES) -> str:
    """sha256 of the squares file; part of the outline fingerprint."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned(zone: int, lon: float, lat: float, squares: frozenset) -> bool:
    return square_of(zone, lon, lat) in squares


def claimer(lon: float, lat: float, squares: frozenset) -> int | None:
    """Zone whose tile keeps an unowned location: the western neighbour if its square there has
    a tile, else the eastern one; None when the location's own zone owns it or nobody can."""
    zl = zone_of(lon, lat)
    if owned(zl, lon, lat, squares):
        return None
    for step in (-1, 1):
        z = neighbour(zl, step)
        if owned(z, lon, lat, squares):
            return z
    return None


def claims(zone: int, points: list[tuple[float, float]], squares: frozenset) -> bool:
    """Does a tile of ``zone`` keep a parcel of its own square that lies outside its zone?

    Any of ``points`` unowned and claimed by ``zone`` is enough, so a field that a cut-off
    neighbour piece would cede is kept whole.
    """
    return any(claimer(lon, lat, squares) == zone for lon, lat in points)


def zone_keeps(
    to_ll: Transformer,
    zn: int,
    band: str,
    centroid: tuple[float, float, float],
    x_west: float,
    x_east: float,
    in_square: bool,
    squares: frozenset,
) -> tuple[bool, bool]:
    """(``in_utm_zone``, ``claimed``) of a parcel.

    The zone test, or a claim for a parcel of the tile's square in an adjacent zone's unowned
    strip, tested at the centroid and every CLAIM_STEP_M along the centroid row across the
    parcel. ``to_ll`` maps the tile CRS to lon/lat; ``centroid`` is (lon, lat, projected
    northing); ``x_west`` and ``x_east`` are the parcel's projected extent.
    """
    lon, lat, ey = centroid
    if lon_in_zone(zn, band, lon):
        return True, False
    if not in_square:
        return False, False
    xs = np.unique(np.r_[np.arange(x_west, x_east, CLAIM_STEP_M), x_east])
    lons, lats = to_ll.transform(xs, np.full(xs.size, ey))
    c = claims(zn, [(lon, lat), *zip(lons, lats, strict=True)], squares)
    return c, c


def build_squares(tiles: set[str], footprints: Path, out: Path) -> int:
    """Owned squares of ``tiles`` from their footprints (a mosaic tile is its 100 km square plus
    a few tens of metres)."""
    import pyarrow.parquet as pq
    import shapely

    fp = pq.read_table(footprints, columns=["tile_key", "geometry", "antimeridian"]).to_pylist()
    rows = set()
    for r in fp:
        if r["tile_key"] not in tiles:
            continue
        z, north = int(r["tile_key"][:2]), r["tile_key"][2] >= "N"
        xy = shapely.get_coordinates(shapely.from_wkb(r["geometry"]))
        e, n = _to_utm(z, north).transform(xy[:, 0], xy[:, 1])
        if r["antimeridian"]:  # the lon/lat footprint is split in two; UTM is not
            e = np.where(np.abs(e - np.median(e)) > 3e5, np.nan, e)
        x0 = int(math.floor((np.nanmin(e) + 5000) / 1e5) * 1e5)
        y1 = int(math.ceil((np.nanmax(n) - 5000) / 1e5) * 1e5)
        rows.add((z, int(north), x0, y1))
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "north", "x0", "y1"])
        w.writerows(sorted(rows))
    return len(rows)


def read_tiles(path: Path) -> set[str]:
    """Tile keys from a text list or a parquet with a ``tile_key`` column."""
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return set(pq.read_table(path, columns=["tile_key"])["tile_key"].to_pylist())
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["squares"])
    ap.add_argument("--keep-list", type=Path, nargs="+", required=True,
                    help="tile lists (text or parquet with tile_key); their union is used")
    ap.add_argument("--footprints", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=SQUARES)
    a = ap.parse_args()
    tiles = set().union(*(read_tiles(p) for p in a.keep_list))
    print(f"{a.out}: {build_squares(tiles, a.footprints, a.out)} squares from {len(tiles)} tiles")


if __name__ == "__main__":
    main()
