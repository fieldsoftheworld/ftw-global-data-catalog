"""Tile index and STAC items of the mosaic mirror, built from its published manifests.

    python3 indexes.py fetch-manifests --out manifests
    python3 indexes.py tile-index --manifests manifests --footprints tile_footprints.parquet \\
        --out-dir index
    python3 indexes.py stac-items --manifests manifests --footprints tile_footprints.parquet \\
        --out-dir stac

``tile_index_{year}.parquet`` has one row per year, quarter and tile with ``{band}_href`` and
``{band}_size`` per band. ``items_{year}.parquet`` holds the same items as stac-geoparquet.
The mirror no longer publishes its ``_status`` completion markers, so every quarter with a
manifest is indexed.
"""

import argparse
import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

BUCKET = "tge-labs"
PREFIX = "sentinel-2-quarterly-cloudless-mosaics"
HTTPS_BASE = "https://data.source.coop"
MIRROR = f"{HTTPS_BASE}/{BUCKET}/{PREFIX}"
# data.source.coop answers 403 to Python's default User-Agent
USER_AGENT = "ftw-global-data-catalog (+https://github.com/fieldsoftheworld)"
YEARS = tuple(range(2017, 2026))
BANDS = {
    "B02": ("blue", 0.493),
    "B03": ("green", 0.560),
    "B04": ("red", 0.665),
    "B08": ("nir", 0.833),
}
QUARTERS = {
    "Q1": ("01-01", "03-31"),
    "Q2": ("04-01", "06-30"),
    "Q3": ("07-01", "09-30"),
    "Q4": ("10-01", "12-31"),
}
ITEM_RE = re.compile(r"^Sentinel-2_mosaic_(\d{4})_(Q[1-4])_(\d{2}[A-Z]{3}_\d+_\d+)$")


def fetch_manifests(out_dir: Path, years=YEARS) -> list[Path]:
    """Download ``manifest_{year}_{quarter}.parquet``; files already present are kept."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for year in years:
        for q in QUARTERS:
            dst = out_dir / f"manifest_{year}_{q}.parquet"
            if not dst.exists():
                req = urllib.request.Request(
                    f"{MIRROR}/manifest/{dst.name}", headers={"User-Agent": USER_AGENT}
                )
                tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
                with urllib.request.urlopen(req, timeout=120) as resp:
                    tmp.write_bytes(resp.read())
                try:
                    if pq.read_metadata(tmp).num_rows == 0:
                        raise ValueError(f"{dst.name}: empty manifest")
                except Exception:
                    tmp.unlink()
                    raise
                os.replace(tmp, dst)
            paths.append(dst)
    return paths


def destination_key(item_id: str, band: str, prefix: str = PREFIX) -> str:
    """The mirror key of one band, as the transfer wrote it."""
    m = ITEM_RE.match(item_id)
    if not m or band not in BANDS:
        raise ValueError(f"unexpected manifest item {item_id!r} band {band!r}")
    year, quarter, tile = m.groups()
    return f"{prefix}/{year}/{quarter}/{tile}/{band}.tif"


def read_manifests(manifest_dir: Path, year: int) -> dict[tuple[str, str], dict[str, int]]:
    """``{(quarter, tile_key): {band: size}}`` for one year, every item with all four bands."""
    shards = sorted(manifest_dir.glob(f"manifest_{year}_Q*.parquet"))
    if not shards:
        raise SystemExit(f"no manifests for {year} under {manifest_dir}")
    items: dict[tuple[str, str], dict[str, int]] = {}
    cols = [
        "year",
        "quarter",
        "item_id",
        "band",
        "size_bytes",
        "destination_bucket",
        "destination_key",
    ]
    for shard in shards:
        t = pq.read_table(shard, columns=cols).to_pydict()
        for y, q, iid, band, size, bucket, key in zip(*t.values(), strict=True):
            if (y, bucket) != (year, BUCKET) or key != destination_key(iid, band):
                raise ValueError(f"{shard.name}: {iid} {band} is not at {bucket}/{key}")
            tile, quarter = ITEM_RE.match(iid).group(3), ITEM_RE.match(iid).group(2)
            if quarter != q:
                raise ValueError(f"{shard.name}: {iid} listed under {q}")
            items.setdefault((quarter, tile), {})[band] = size
    short = [k for k, v in items.items() if len(v) != len(BANDS)]
    if short:
        raise ValueError(f"{year}: {len(short)} items lack a band, e.g. {short[0]}")
    return items


def load_footprints(path: Path) -> dict[str, bytes]:
    t = pq.read_table(path, columns=["tile_key", "geometry"]).to_pydict()
    return dict(zip(t["tile_key"], t["geometry"], strict=True))


def hilbert_order(x: np.ndarray, y: np.ndarray, level: int = 16) -> np.ndarray:
    """Argsort by Hilbert distance, so near tiles share row groups."""
    n = 1 << level

    def scale(v):
        return ((v - v.min()) / max(np.ptp(v), 1e-12) * (n - 1)).astype(np.int64)

    xi, yi, d = scale(x), scale(y), np.zeros(len(x), np.int64)
    s = n >> 1
    while s:
        rx, ry = (xi & s) > 0, (yi & s) > 0
        d += s * s * ((3 * rx) ^ ry)
        flip = ~ry & rx
        xi, yi = np.where(flip, n - 1 - xi, xi), np.where(flip, n - 1 - yi, yi)
        xi, yi = np.where(~ry, yi, xi), np.where(~ry, xi, yi)
        s >>= 1
    return np.argsort(d, kind="stable")


def _rows(year: int, manifest_dir: Path, footprints: Path):
    """Items in Hilbert order with their footprint; tiles without one are dropped."""
    items = read_manifests(manifest_dir, year)
    geoms = load_footprints(footprints)
    missing = {tile for _, tile in items} - geoms.keys()
    if missing:
        print(f"{year}: {len(missing):,} tiles have no footprint; dropped")
    keys = [k for k in items if k[1] in geoms]
    shapes = shapely.from_wkb([geoms[t] for _, t in keys])
    bounds = shapely.bounds(shapes)
    order = hilbert_order((bounds[:, 0] + bounds[:, 2]) / 2, (bounds[:, 1] + bounds[:, 3]) / 2)
    return [(keys[i][0], keys[i][1], items[keys[i]], shapes[i], bounds[i]) for i in order]


def _href(year: int, quarter: str, tile: str, band: str) -> str:
    return f"{MIRROR}/{year}/{quarter}/{tile}/{band}.tif"


def _span(year: int, quarter: str) -> tuple[datetime, datetime]:
    start, end = QUARTERS[quarter]
    return (
        datetime.fromisoformat(f"{year}-{start}T00:00:00").replace(tzinfo=UTC),
        datetime.fromisoformat(f"{year}-{end}T23:59:59").replace(tzinfo=UTC),
    )


def _geo_metadata(shapes, covering: dict | None = None) -> bytes:
    types = sorted(set(shapely.get_type_id(shapes).tolist()))
    names = {3: "Polygon", 6: "MultiPolygon"}
    col = {
        "encoding": "WKB",
        "geometry_types": [names[t] for t in types],
        "bbox": list(shapely.total_bounds(shapes)),
    }
    if covering:
        col["covering"] = covering
    return json.dumps(
        {"version": "1.1.0", "primary_column": "geometry", "columns": {"geometry": col}}
    ).encode()


def _write(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    pq.write_table(table, tmp, compression="zstd", row_group_size=50_000)
    os.replace(tmp, path)


def tile_index(year: int, manifest_dir: Path, footprints: Path) -> pa.Table:
    rows = _rows(year, manifest_dir, footprints)
    shapes = np.array([r[3] for r in rows])
    bounds = np.array([r[4] for r in rows])
    spans = [_span(year, r[0]) for r in rows]
    cols = {
        "year": pa.array([year] * len(rows), pa.int64()),
        "quarter": pa.array([r[0] for r in rows]),
        "tile_key": pa.array([r[1] for r in rows]),
    }
    for band in BANDS:
        cols[f"{band.lower()}_size"] = pa.array([r[2][band] for r in rows], pa.int64())
    cols["geometry"] = pa.array(shapely.to_wkb(shapes).tolist(), pa.binary())
    for band in BANDS:
        cols[f"{band.lower()}_href"] = pa.array([_href(year, r[0], r[1], band) for r in rows])
    ts = pa.timestamp("ms", tz="UTC")
    cols["start_datetime"] = pa.array([s for s, _ in spans], ts)
    cols["end_datetime"] = pa.array([e for _, e in spans], ts)
    for i, name in enumerate(("xmin", "ymin", "xmax", "ymax")):
        cols[name] = pa.array(bounds[:, i], pa.float64())
    table = pa.table(cols)
    return table.replace_schema_metadata({b"geo": _geo_metadata(shapes)})


def proj_code(tile_key: str) -> str:
    """UTM EPSG from the MGRS band letter (N-X north, C-M south)."""
    return f"EPSG:32{6 if tile_key[2] >= 'N' else 7}{tile_key[:2]}"


_ASSET = pa.struct(
    [
        ("href", pa.string()),
        ("type", pa.string()),
        ("roles", pa.list_(pa.string())),
        ("nodata", pa.float64()),
        ("data_type", pa.string()),
        ("alternate", pa.struct([("s3", pa.struct([("href", pa.string())]))])),
        (
            "eo:bands",
            pa.list_(
                pa.struct(
                    [
                        ("name", pa.string()),
                        ("common_name", pa.string()),
                        ("center_wavelength", pa.float64()),
                    ]
                )
            ),
        ),
        ("file:size", pa.int64()),
    ]
)
_BBOX = pa.struct([(k, pa.float64()) for k in ("xmin", "ymin", "xmax", "ymax")])
STAC_SCHEMA = pa.schema(
    [
        ("type", pa.string()),
        ("stac_version", pa.string()),
        ("id", pa.string()),
        ("quarter", pa.string()),
        ("tile_key", pa.string()),
        ("links", pa.list_(pa.null())),
        ("assets", pa.struct([(b, _ASSET) for b in BANDS])),
        ("collection", pa.string()),
        ("datetime", pa.timestamp("ms", tz="UTC")),
        ("start_datetime", pa.timestamp("ms", tz="UTC")),
        ("end_datetime", pa.timestamp("ms", tz="UTC")),
        ("gsd", pa.int64()),
        ("proj:code", pa.string()),
        ("proj:shape", pa.list_(pa.int64())),
        ("constellation", pa.string()),
        ("instruments", pa.list_(pa.string())),
        ("product:type", pa.string()),
        ("processing:level", pa.string()),
        ("grid:code", pa.string()),
        ("bbox", _BBOX),
        ("geometry", pa.binary()),
    ]
)


def stac_items(year: int, manifest_dir: Path, footprints: Path) -> pa.Table:
    rows = _rows(year, manifest_dir, footprints)
    out = []
    for quarter, tile, sizes, geom, (w, s, e, n) in rows:
        start, end = _span(year, quarter)
        assets = {}
        for band, (common, wl) in BANDS.items():
            key = f"{PREFIX}/{year}/{quarter}/{tile}/{band}.tif"
            assets[band] = {
                "href": _href(year, quarter, tile, band),
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"],
                "nodata": -32768.0,
                "data_type": "int16",
                "alternate": {"s3": {"href": f"s3://{BUCKET}/{key}"}},
                "eo:bands": [{"name": band, "common_name": common, "center_wavelength": wl}],
                "file:size": sizes[band],
            }
        out.append(
            {
                "type": "Feature",
                "stac_version": "1.1.0",
                "id": f"Sentinel-2_mosaic_{year}_{quarter}_{tile}",
                "quarter": quarter,
                "tile_key": tile,
                "links": [],
                "assets": assets,
                "collection": PREFIX,
                "datetime": None,
                "start_datetime": start,
                "end_datetime": end,
                "gsd": 10,
                "proj:code": proj_code(tile),
                "proj:shape": [10008, 10008],
                "constellation": "sentinel-2",
                "instruments": ["msi"],
                "product:type": "S2MSI_L3__MCQ",
                "processing:level": "L3",
                "grid:code": f"MGRS-{tile.split('_')[0]}",
                "bbox": {"xmin": w, "ymin": s, "xmax": e, "ymax": n},
                "geometry": shapely.to_wkb(geom),
            }
        )
    table = pa.Table.from_pylist(out, schema=STAC_SCHEMA)
    covering = {"bbox": {k: ["bbox", k] for k in ("xmin", "ymin", "xmax", "ymax")}}
    shapes = np.array([r[3] for r in rows])
    return table.replace_schema_metadata(
        {
            b"geo": _geo_metadata(shapes, covering),
            b"stac-geoparquet": json.dumps({"version": "1.0.0"}).encode(),
        }
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-manifests")
    f.add_argument("--out", type=Path, default=Path("manifests"))
    f.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    for name in ("tile-index", "stac-items"):
        p = sub.add_parser(name)
        p.add_argument("--manifests", type=Path, default=Path("manifests"))
        p.add_argument("--footprints", type=Path, required=True)
        p.add_argument("--out-dir", type=Path, required=True)
        p.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    a = ap.parse_args()
    if a.cmd == "fetch-manifests":
        print(f"{len(fetch_manifests(a.out, a.years))} manifests in {a.out}")
        return
    build, stem = (tile_index, "tile_index") if a.cmd == "tile-index" else (stac_items, "items")
    for year in a.years:
        table = build(year, a.manifests, a.footprints)
        dst = a.out_dir / f"{stem}_{year}.parquet"
        _write(table, dst)
        print(f"{year}: {table.num_rows:,} items -> {dst}")


if __name__ == "__main__":
    main()
