"""Compact raw outline tiles to UTM-grid integer deltas (lossless), or expand them back to WKB.

encode: {in-root}/YEAR/TILE.parquet -> {out-root}/YEAR/TILE.parquet. The compact file is read
        back and must hold exactly the original attributes and the grid integers of the original
        coordinates (same rings and vertices); only then is it moved into place (and the original
        removed with --delete-original).
decode: compact file -> GeoParquet-compatible WKB file with the original geo metadata.
Outlines live on the 2.5 m UTM grid; compact files store grid integers; decoding gives coordinates
equal to the original to float precision (the PROJ inverse can differ in the last bit between
machines).
Usage:
    python pipeline/postprocessing/compact_outlines.py encode --year 2023 [--in-root outlines]
        [--out-root outlines-compact] [--shard 0 --num-shards 8] [--min-mb 300] [--delete-original]
    python pipeline/postprocessing/compact_outlines.py decode IN.parquet OUT.parquet
"""

import argparse
import json
import os
import sys
import zlib
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from outline_codec import CODEC_COLUMNS, RES, decode, encode, utm_epsg

CHUNK = 100_000
META_KEY = b"compact_outlines"
GEO_KEY = b"geo"


def _wkb(col):
    return np.asarray(col.to_numpy(zero_copy_only=False), dtype=object)


def _writer(path, schema):
    return pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        compression_level=19,
        use_dictionary=False,
        column_encoding={
            "dx.list.element": "DELTA_BINARY_PACKED",
            "dy.list.element": "DELTA_BINARY_PACKED",
        },
    )


def _same(a, b):
    if a.equals(b):
        return True
    if pa.types.is_floating(a.type):
        return a.null_count == b.null_count and np.array_equal(
            a.to_numpy(zero_copy_only=False), b.to_numpy(zero_copy_only=False), equal_nan=True
        )
    return False


def check_source_stat(src, original: os.stat_result) -> None:
    """Reject a source file replaced or changed during compaction."""
    current = Path(src).stat()
    if (current.st_size, current.st_mtime_ns) != (original.st_size, original.st_mtime_ns):
        raise ValueError("source changed during compaction")


def encode_file(src, dst, tile_key):
    epsg = utm_epsg(tile_key)
    source_stat = Path(src).stat()
    table = pq.read_table(src)
    attrs = table.drop_columns(["geometry"])
    wkb = table["geometry"]
    meta = dict(table.schema.metadata or {})
    meta = {
        META_KEY: json.dumps({"version": 1, "epsg": epsg, "res_m": RES}).encode(),
        b"geo_original": meta[GEO_KEY],
    }
    tmp = f"{dst}.tmp-{os.getpid()}"
    writer = None
    try:
        for i in range(0, table.num_rows, CHUNK):
            n = min(CHUNK, table.num_rows - i)
            geoms = shapely.from_wkb(_wkb(wkb.slice(i, n)))
            cols = encode(geoms, epsg)
            batch = attrs.slice(i, n).combine_chunks()
            for name in CODEC_COLUMNS:
                batch = batch.append_column(name, cols[name])
            if writer is None:
                writer = _writer(tmp, batch.schema.with_metadata(meta))
            writer.write_table(batch)
        if writer is None:
            raise ValueError("empty tile")
        writer.close()
        _verify(tmp, table, epsg)
        check_source_stat(src, source_stat)
        os.replace(tmp, dst)
    finally:
        if writer is not None:
            writer.close()
        if os.path.exists(tmp):
            os.remove(tmp)


def _verify(path, original, epsg):
    """The compact file holds the original attributes and the grid integers of the original geometry."""
    back = pq.read_table(path)
    if back.num_rows != original.num_rows:
        raise ValueError("row count differs")
    for name in original.column_names:
        if name != "geometry" and not _same(back[name], original[name]):
            raise ValueError(f"column {name} differs")
    for i in range(0, back.num_rows, CHUNK):
        part = back.slice(i, CHUNK)
        want = encode(shapely.from_wkb(_wkb(original["geometry"].slice(i, len(part)))), epsg)
        for name in CODEC_COLUMNS:
            if not pa.chunked_array([want[name]]).equals(part[name]):
                raise ValueError(f"grid integers differ in {name}, rows {i}..{i + len(part)}")


def decode_file(src, dst):
    table = pq.read_table(src)
    meta = table.schema.metadata
    epsg = json.loads(meta[META_KEY])["epsg"]
    pieces = []
    for i in range(0, table.num_rows, CHUNK):
        part = table.slice(i, CHUNK)
        geoms = decode({k: part[k] for k in CODEC_COLUMNS}, epsg)
        pieces.append(pa.array(shapely.to_wkb(geoms), type=pa.binary()))
    out = table.drop_columns(CODEC_COLUMNS).append_column("geometry", pa.chunked_array(pieces))
    out = out.replace_schema_metadata({GEO_KEY: meta[b"geo_original"]})
    pq.write_table(out, dst, compression="zstd")


def _encode_year(a):
    src_dir, dst_dir = Path(a.in_root, a.year), Path(a.out_root, a.year)
    dst_dir.mkdir(parents=True, exist_ok=True)
    sizes = {p: p.stat().st_size for p in src_dir.glob("*.parquet")}
    files = sorted(
        (
            p
            for p, n in sizes.items()
            if a.min_mb * 1e6 <= n < a.max_mb * 1e6
            and zlib.crc32(p.name.encode()) % a.num_shards == a.shard
        ),
        key=lambda p: (-sizes[p], p.name),
    )
    done = failed = saved = 0
    for src in files:
        dst = dst_dir / src.name
        try:
            source_stat = src.stat()
            before = source_stat.st_size
            if not dst.exists():
                encode_file(src, dst, src.stem)
            else:
                _verify(dst, pq.read_table(src), utm_epsg(src.stem))
            after = dst.stat().st_size
            if a.delete_original:
                check_source_stat(src, source_stat)
                src.unlink()
            done, saved = done + 1, saved + before - after
            print(f"ok {src.name} {before / 1e6:.0f}->{after / 1e6:.1f} MB", flush=True)
        except Exception as e:
            failed += 1
            print(f"FAIL {src.name}: {type(e).__name__}: {str(e)[:200]}", flush=True)
    print(
        f"{a.year} shard {a.shard}/{a.num_shards}: {done} ok, {failed} failed, saved {saved / 1e9:.1f} GB"
    )
    return failed


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode")
    e.add_argument("--year", required=True)
    e.add_argument("--in-root", default="outlines", help="raw outlines, {in-root}/{year}/{tile}.parquet")
    e.add_argument("--out-root", default="outlines-compact")
    e.add_argument("--shard", type=int, default=0)
    e.add_argument("--num-shards", type=int, default=1)
    e.add_argument("--min-mb", type=float, default=0)
    e.add_argument("--max-mb", type=float, default=float("inf"))
    e.add_argument("--delete-original", action="store_true")
    d = sub.add_parser("decode")
    d.add_argument("src")
    d.add_argument("dst")
    a = ap.parse_args()
    if a.cmd == "decode":
        decode_file(a.src, a.dst)
        return
    sys.exit(1 if _encode_year(a) else 0)


if __name__ == "__main__":
    main()
