"""Download quarterly band COGs and assemble model-ready RGBN stacks."""

import argparse
import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import numpy as np
import rasterio
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from rasterio.windows import Window
from stac import search_items

BANDS = ("B04", "B03", "B02", "B08")
QUARTERS = ("Q1", "Q2", "Q3", "Q4")


def download_asset(asset, dst: Path) -> str:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("EODATA_S3_ENDPOINT", "https://eodata.dataspace.copernicus.eu"),
        aws_access_key_id=os.environ["EODATA_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["EODATA_S3_SECRET_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 10, "mode": "adaptive"},
        ),
    )
    obj = client.get_object(Bucket=asset.s3_bucket, Key=asset.s3_key)
    checksum = hashlib.sha256()
    size = 0
    with obj["Body"] as body, dst.open("wb") as fh:
        for chunk in body.iter_chunks(1 << 20):
            fh.write(chunk)
            checksum.update(chunk)
            size += len(chunk)
    if size != obj["ContentLength"] or (asset.size_bytes and size != asset.size_bytes):
        raise RuntimeError("download size mismatch")
    return checksum.hexdigest()


def stack_bands(paths: list[Path], dst: Path, tags: dict) -> None:
    from contextlib import ExitStack

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
    with ExitStack() as context:
        bands = [context.enter_context(rasterio.open(p)) for p in paths]
        first = bands[0]
        for band in bands:
            if (
                band.count != 1
                or band.shape != first.shape
                or band.crs != first.crs
                or band.transform != first.transform
                or band.dtypes != first.dtypes
            ):
                raise ValueError("quarterly bands must share one grid and dtype")
        profile = first.profile.copy()
        profile.update(
            driver="GTiff",
            count=len(bands),
            tiled=True,
            blockxsize=512,
            blockysize=512,
            compress="ZSTD",
            zstd_level=3,
            predictor=2,
            BIGTIFF="IF_SAFER",
            interleave="band",
        )
        profile.pop("photometric", None)
        with rasterio.open(tmp, "w", **profile) as out:
            for row in range(0, first.height, 512):
                for col in range(0, first.width, 512):
                    win = Window(
                        col, row, min(512, first.width - col), min(512, first.height - row)
                    )
                    out.write(np.stack([b.read(1, window=win) for b in bands]), window=win)
            for i, (q, band) in enumerate(((q, b) for q in QUARTERS for b in BANDS), 1):
                out.set_band_description(i, f"{q}_{band}")
            out.update_tags(**tags)
    os.replace(tmp, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--index-output", type=Path, help="optional B04 source index for polygon QA")
    ap.add_argument("--scratch-dir", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tile-list", type=Path)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    a = ap.parse_args()
    if a.workers < 1 or a.num_shards < 1 or not 0 <= a.shard < a.num_shards:
        ap.error("invalid workers or shard")
    items = search_items(tuple(a.bbox), a.year, QUARTERS, bands=BANDS)
    by_tile = {}
    for item in items:
        quarters = by_tile.setdefault(item.tile_key, {})
        if item.quarter in quarters:
            raise ValueError(f"duplicate quarter for {item.tile_key}")
        quarters[item.quarter] = item
    selected = set(a.tile_list.read_text().split()) if a.tile_list else set(by_tile)
    missing = selected - set(by_tile)
    if missing:
        raise ValueError(f"tiles absent from STAC query: {sorted(missing)}")
    tiles = sorted(selected)[a.shard :: a.num_shards]
    if not tiles:
        ap.error("no tiles for this query/shard")
    a.output_dir.mkdir(parents=True, exist_ok=True)
    a.scratch_dir.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for tile in tiles:
        quarters = by_tile[tile]
        if set(quarters) != set(QUARTERS):
            raise ValueError(f"{tile}: missing quarters {set(QUARTERS) - set(quarters)}")
        sources = [quarters[q] for q in QUARTERS]
        for item in sources:
            asset = item.assets["B04"]
            if a.index_output and not asset.https_href:
                raise ValueError(
                    f"{item.item_id}: no HTTPS alternate for the polygon-QA index"
                )
            if a.index_output:
                index_rows.append(
                    {
                        "tile_key": tile,
                        "quarter": item.quarter,
                        "b04_href": asset.https_href,
                    }
                )
        signature = json.dumps([i.item_id for i in sources])
        dst = a.output_dir / f"{tile}.tif"
        if dst.exists():
            with rasterio.open(dst) as ds:
                if ds.count == 16 and ds.tags().get("source_items") == signature:
                    print(f"{tile}: current", flush=True)
                    continue
        with tempfile.TemporaryDirectory(dir=a.scratch_dir, prefix=f"{tile}-") as scratch:
            jobs = [
                (quarters[q].assets[b], Path(scratch) / f"{q}_{b}.tif")
                for q in QUARTERS
                for b in BANDS
            ]
            with ThreadPoolExecutor(a.workers) as pool:
                sums = list(pool.map(lambda job: download_asset(*job), jobs))
            stack_bands(
                [path for _, path in jobs],
                dst,
                {
                    "source_collection": "sentinel-2-global-mosaics",
                    "source_items": signature,
                    "source_sha256": json.dumps(sums),
                    "year": str(a.year),
                    "input_bands": "Q1,Q2,Q3,Q4 x B04,B03,B02,B08",
                },
            )
        print(f"{tile}: {dst}", flush=True)

    if a.index_output:
        a.index_output.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.index_output.with_name(f"{a.index_output.name}.tmp-{os.getpid()}")
        pq.write_table(pa.Table.from_pylist(index_rows), tmp, compression="zstd")
        os.replace(tmp, a.index_output)


if __name__ == "__main__":
    main()
