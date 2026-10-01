"""Download quarterly band COGs and assemble model-ready RGBN stacks."""

import argparse
import hashlib
import json
import os
import tempfile
import threading
import uuid
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
# One constant drives the download jobs, the band descriptions and the tag.
BAND_KEYS = tuple(f"{q}_{b}" for q in QUARTERS for b in BANDS)
EODATA_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu"

_local = threading.local()


def eodata_endpoint() -> str:
    "The EODATA S3 endpoint this run reads through, and the index records."
    return os.environ.get("EODATA_S3_ENDPOINT", EODATA_S3_ENDPOINT)


def s3_client():
    """One client per thread, each on its own session.

    ``boto3``'s default session is documented as not thread-safe, and a client
    per asset would also throw away every pooled connection.
    """
    client = getattr(_local, "s3", None)
    if client is None:
        client = boto3.Session().client(
            "s3",
            endpoint_url=eodata_endpoint(),
            aws_access_key_id=os.environ["EODATA_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["EODATA_S3_SECRET_KEY"],
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 10, "mode": "adaptive"},
            ),
        )
        _local.s3 = client
    return client


def download_asset(asset, dst: Path) -> str:
    client = s3_client()
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
    if len(paths) != len(BAND_KEYS):
        raise ValueError(f"expected {len(BAND_KEYS)} band paths, got {len(paths)}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp-{uuid.uuid4().hex}")
    try:
        _write_stack(paths, tmp, tags)
        os.replace(tmp, dst)
    except BaseException:  # no partial stack survives a failed run
        tmp.unlink(missing_ok=True)
        raise


def _write_stack(paths: list[Path], tmp: Path, tags: dict) -> None:
    from contextlib import ExitStack

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
            for i, key in enumerate(BAND_KEYS, 1):
                out.set_band_description(i, key)
            out.update_tags(**tags)


def bbox_error(bbox: list[float], *, allow_antimeridian: bool) -> str | None:
    "Return why ``bbox`` is unusable as a (W, S, E, N) geographic box, else None."
    w, s, e, n = bbox
    if not all(-180 <= v <= 180 for v in (w, e)):
        return f"--bbox longitudes must be within [-180, 180], got W={w} E={e}"
    if not all(-90 <= v <= 90 for v in (s, n)):
        return f"--bbox latitudes must be within [-90, 90], got S={s} N={n}"
    if s >= n:
        return f"--bbox needs S < N, got S={s} N={n}"
    if w == e:
        return f"--bbox needs W < E, got a zero-width box at W=E={w}"
    if w > e and not allow_antimeridian:
        return (
            f"--bbox needs W < E, got W={w} E={e}; pass --allow-antimeridian "
            "to query a box that crosses the antimeridian"
        )
    return None


def index_row(tile: str, item) -> dict:
    """One polygon-QA index row for a mosaic item's B04 asset.

    Two access forms in distinct columns, because they are not
    interchangeable: ``b04_s3_href`` is the EODATA S3 object this pipeline
    itself reads (keys in the environment, ``b04_s3_endpoint``, GDAL
    ``/vsis3/<bucket>/<key>``) and is the one a reader should open;
    ``b04_odata_href`` is the item's CDSE OData alternate, which needs an OIDC
    bearer token and whose ``/$value`` path GDAL's extension check rejects, so
    it is recorded for provenance only.
    """
    asset = item.assets["B04"]
    if not asset.s3_bucket or not asset.s3_key:
        raise ValueError(f"{item.item_id}: B04 asset has no S3 location for the polygon-QA index")
    return {
        "tile_key": tile,
        "quarter": item.quarter,
        "b04_s3_href": f"s3://{asset.s3_bucket}/{asset.s3_key}",
        "b04_s3_endpoint": eodata_endpoint(),
        "b04_odata_href": asset.https_href,
    }


def shard_index_path(path: Path, shard: int, num_shards: int) -> Path:
    "Shard-scoped index path, so one shard's rows never replace another's."
    if num_shards == 1:
        return path
    return path.with_suffix(f".shard-{shard}-of-{num_shards}{path.suffix}")


def is_current(dst: Path, signature: str) -> bool:
    "True when an existing stack is a complete stack of exactly these sources."
    try:
        with rasterio.open(dst) as ds:
            return ds.count == len(BAND_KEYS) and ds.tags().get("source_items") == signature
    except Exception as exc:  # noqa: BLE001 - an unusable stack is simply rebuilt
        print(f"{dst}: unreadable ({exc}); rebuilding", flush=True)
        return False


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
    ap.add_argument(
        "--allow-antimeridian",
        action="store_true",
        help="accept a --bbox whose W is east of its E (crosses 180)",
    )
    a = ap.parse_args()
    if a.workers < 1 or a.num_shards < 1 or not 0 <= a.shard < a.num_shards:
        ap.error("invalid workers or shard")
    problem = bbox_error(a.bbox, allow_antimeridian=a.allow_antimeridian)
    if problem:
        ap.error(problem)
    index_output = (
        shard_index_path(a.index_output, a.shard, a.num_shards) if a.index_output else None
    )
    items = search_items(tuple(a.bbox), a.year, QUARTERS, bands=BANDS)
    by_tile = {}
    for item in items:
        quarters = by_tile.setdefault(item.tile_key, {})
        if item.quarter in quarters:
            raise ValueError(f"duplicate quarter for {item.tile_key}")
        quarters[item.quarter] = item
    if a.tile_list:
        # The intended tile set is the list, so absences are real findings.
        selected = set(a.tile_list.read_text().split())
        missing = selected - set(by_tile)
        if missing:
            raise ValueError(f"tiles absent from STAC query: {sorted(missing)}")
    else:
        # No intended set to compare against: every tile the query reports is
        # selected, and search_items is what guarantees the pages are complete.
        selected = set(by_tile)
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
        if index_output:
            index_rows.extend(index_row(tile, item) for item in sources)
        signature = json.dumps([i.item_id for i in sources])
        dst = a.output_dir / f"{tile}.tif"
        if dst.exists() and is_current(dst, signature):
            print(f"{tile}: current", flush=True)
            continue
        with tempfile.TemporaryDirectory(dir=a.scratch_dir, prefix=f"{tile}-") as scratch:
            jobs = []
            for key in BAND_KEYS:
                quarter, band = key.split("_")
                jobs.append((quarters[quarter].assets[band], Path(scratch) / f"{key}.tif"))
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
                    "input_bands": ",".join(BAND_KEYS),
                },
            )
        print(f"{tile}: {dst}", flush=True)

    if index_output:
        index_output.parent.mkdir(parents=True, exist_ok=True)
        tmp = index_output.with_name(f"{index_output.name}.tmp-{uuid.uuid4().hex}")
        try:
            pq.write_table(pa.Table.from_pylist(index_rows), tmp, compression="zstd")
            os.replace(tmp, index_output)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        print(f"index: {index_output} ({len(index_rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
