#!/usr/bin/env python3
"""The data upload contract: two gates, and no reimplementation.

tools/upload_data.py walks a staging directory that sits outside the published
catalog. It admits a file only when both gates pass. The path gate admits only
files under data_dir. The extension gate admits only the suffixes in
PUBLISHABLE_SUFFIXES.

This gate also checks that the script refuses an unedited config, that it
exits with a message when data_dir is wrong, and that the multipart ETag it
computes for verification is the one S3 gives the object.

No network, no AWS, no credentials.

Run: python3 tests/test_upload_data.py
"""
import hashlib
import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import upload_data  # noqa: E402
from publish import Upload  # noqa: E402
from upload_data import (  # noqa: E402
    MIB,
    PUBLISHABLE_SUFFIXES,
    collect_data_uploads,
    data_root,
    expected_etag,
    is_data_publishable,
    mismatches,
    part_size,
    unedited_sentinels,
)

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def exit_message(call) -> str:
    """Run a call that must exit, and return the message it exits with."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            call()
    except SystemExit as exc:
        return str(exc.code)
    return ""


# --- the extension allow-list ------------------------------------------
check(".parquet" in PUBLISHABLE_SUFFIXES, "parquet is publishable")
check(".pmtiles" in PUBLISHABLE_SUFFIXES, "pmtiles is publishable")
check(".tif" in PUBLISHABLE_SUFFIXES, "tif is publishable")
check(".geojson" not in PUBLISHABLE_SUFFIXES, "geojson scratch never uploads")
check(is_data_publishable(Path("a/fields.parquet")), "parquet passes")
check(is_data_publishable(Path("a/fields.PARQUET")), "suffix case is ignored")
check(is_data_publishable(Path("a/fields.pmtiles")), "pmtiles passes")
check(not is_data_publishable(Path("a/scratch.geojson")), "geojson is barred")
check(not is_data_publishable(Path("a/notes.md")), "markdown is barred")
check(not is_data_publishable(Path("a/x.parquet.tmp")), "tmp is barred")
check(not is_data_publishable(Path("a/.work/x.parquet")), "dotdir is barred")

# --- the path gate and the walk ----------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # Staged data. Both gates pass.
    write(root / "staging-data/vector/fields-yearly/fields-2025.pmtiles")
    write(root / "staging-data/vector/fields-yearly/cells_a5r7_2025.parquet")
    write(root / "staging-data/raster/2025/overview.tif")

    # Staged scratch. The extension gate bars it.
    write(root / "staging-data/scratch/fields.geojson")
    write(root / "staging-data/scratch/notes.md")
    write(root / "staging-data/.work/tmp.parquet")

    # Outside the staging tree. The path gate bars it.
    write(root / "catalog/catalog.json")
    write(root / "elsewhere/stray.parquet")
    write(root / "stray-at-root.pmtiles")

    config = {
        "write_prefix": "s3://a-bucket/ftw/global-data-2e",
        "public_base": "https://data.example.org/ftw/global-data-2e",
        "publish_dir": "catalog",
        "data_dir": "staging-data",
    }
    uploads = collect_data_uploads(config, root)
    keys = {u.key for u in uploads}

    expected = {
        "ftw/global-data-2e/vector/fields-yearly/fields-2025.pmtiles",
        "ftw/global-data-2e/vector/fields-yearly/cells_a5r7_2025.parquet",
        "ftw/global-data-2e/raster/2025/overview.tif",
    }
    check(keys == expected, f"upload set wrong.\n  extra:   {keys - expected}"
                            f"\n  missing: {expected - keys}")

    # Content types come from publish.py, not from a second table.
    types = {u.key: u.content_type for u in uploads}
    check(
        types["ftw/global-data-2e/vector/fields-yearly/cells_a5r7_2025.parquet"]
        == "application/vnd.apache.parquet",
        "parquet content type comes from publish.py",
    )
    check(
        types["ftw/global-data-2e/vector/fields-yearly/fields-2025.pmtiles"]
        == "application/vnd.pmtiles",
        "pmtiles content type comes from publish.py",
    )

    # An absolute data_dir works too.
    absolute = dict(config, data_dir=str(root / "staging-data"))
    check(
        {u.key for u in collect_data_uploads(absolute, root)} == expected,
        "an absolute data_dir walks the same tree",
    )

    # --- an absent or wrong data_dir exits with a message ---------------
    message = exit_message(lambda: data_root(dict(config, data_dir=""), root))
    check("data_dir" in message, f"an empty data_dir names the key: {message}")

    no_key = {k: v for k, v in config.items() if k != "data_dir"}
    check(
        "data_dir" in exit_message(lambda: data_root(no_key, root)),
        "an absent data_dir names the key",
    )

    missing = dict(config, data_dir="no-such-directory")
    check(
        "does not exist" in exit_message(lambda: data_root(missing, root)),
        "a data_dir that does not exist says so",
    )

# --- the sentinel guard ------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    write(root / "staging-data/fields.parquet")
    sentinel_config = {
        "write_prefix": "s3://EXAMPLE-BUCKET/EXAMPLE-PREFIX",
        "public_base": "https://example.invalid/EXAMPLE-PREFIX",
        "publish_dir": "catalog",
        "data_dir": str(root / "staging-data"),
    }
    check(unedited_sentinels(sentinel_config) != [],
          "an unedited config is refused")
    out = io.StringIO()
    argv = sys.argv
    sys.argv = ["upload_data.py", "--confirm"]
    real_load = upload_data.load_config
    upload_data.load_config = lambda *a, **k: sentinel_config
    try:
        with redirect_stdout(out):
            code = upload_data.main()
    finally:
        upload_data.load_config = real_load
        sys.argv = argv
    check(code == 1, f"the sentinel guard refuses to upload, got {code}")
    check("EXAMPLE-BUCKET" in out.getvalue(),
          "the guard names the sentinel it found")

# --- multipart ETags ----------------------------------------------------
check(part_size(150_019_978, 18) == 8 * MIB, "18 parts of 143 MiB are 8 MiB parts")
check(part_size(150_019_978, 9) == 16 * MIB, "9 parts of 143 MiB are 16 MiB parts")
check(part_size(200_000 * MIB, 10_000) == 20 * MIB,
      "past 10,000 parts the CLI grows the part to a whole MiB")


def s3_etag(data: bytes, part: int) -> str:
    """What S3 reports for ``data`` uploaded in ``part``-byte parts."""
    chunks = [data[i:i + part] for i in range(0, len(data), part)]
    joined = b"".join(hashlib.md5(c).digest() for c in chunks)
    return f"{hashlib.md5(joined).hexdigest()}-{len(chunks)}"


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    blob = bytes(range(256)) * (81_920 + 3)  # 20 MiB and a little
    big = root / "big.tif"
    big.write_bytes(blob)
    small = write(root / "small.parquet", "small")
    for part in (8 * MIB, 16 * MIB):
        tag = s3_etag(blob, part)
        check(expected_etag(big, tag) == tag, f"multipart ETag at {part // MIB} MiB parts")
    single = hashlib.md5(b"small").hexdigest()
    check(expected_etag(small, f'"{single}"') == single, "a single-part ETag is the MD5")

    ups = [Upload(big, "p/raster/big.tif", "x"), Upload(small, "p/index/small.parquet", "x")]
    good = {"p/raster/big.tif": (len(blob), s3_etag(blob, 8 * MIB)),
            "p/index/small.parquet": (5, single)}
    check(not any(mismatches(ups, good).values()), "matching objects pass")
    bad = mismatches(ups, {"p/raster/big.tif": (len(blob), s3_etag(blob[::-1], 8 * MIB))})
    check(bad == {"missing": ["p/index/small.parquet"], "size": [],
                  "etag": ["p/raster/big.tif"]},
          f"a same-size object with other bytes fails on its ETag: {bad}")
    short = mismatches(ups[1:], {"p/index/small.parquet": (4, single)})
    check(short["size"] == ["p/index/small.parquet"], "a truncated object fails on size")

    # --verify-only exits 1 on a mismatch and 0 when the bucket matches
    config = {
        "write_prefix": "s3://a-bucket/p",
        "public_base": "https://data.example.org/p",
        "publish_dir": "catalog",
        "data_dir": str(root / "staging"),
    }
    write(root / "staging/index/small.parquet", "small")
    argv, real_load, real_index = sys.argv, upload_data.load_config, upload_data.remote_data_index
    sys.argv = ["upload_data.py", "--verify-only"]
    upload_data.load_config = lambda *a, **k: config
    try:
        codes = []
        for listing in ({"p/index/small.parquet": (5, single)},
                        {"p/index/small.parquet": (5, "0" * 32)}):
            upload_data.remote_data_index = lambda u, c, listing=listing: listing
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                codes.append(upload_data.main())
    finally:
        sys.argv, upload_data.load_config = argv, real_load
        upload_data.remote_data_index = real_index
    check(codes == [0, 1], f"--verify-only exits 0 on a match and 1 on a mismatch: {codes}")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: data upload contract holds")
