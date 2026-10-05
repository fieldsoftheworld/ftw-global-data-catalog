#!/usr/bin/env python3
"""The publish contract: only the published directory is ever uploaded.

This is the gate that turns the three-file-category model into an enforced
property. It builds a temp tree holding all three categories, asks the
publisher what it would upload, and asserts set equality — so a leak fails and
a missing file fails too. It also covers change detection, content types, the
non-recursive listing plan, the sentinel guard, and the retrying upload pool
(with a stubbed aws CLI).

No network, no AWS, no credentials.

Run: python3 tests/test_publish.py
"""
import hashlib
import io
import json
import re
import subprocess
import tempfile
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import publish  # noqa: E402
from publish import (  # noqa: E402
    Upload,
    collect_uploads,
    content_type_for,
    is_unchanged,
    key_dirs,
    remote_index,
    split_s3_uri,
    unedited_sentinels,
    upload_all,
)

errors: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# This suite drives main(), which re-records the raster snapshot on a
# successful publish. Nothing here may touch the committed file.
SNAPSHOT_BEFORE = publish.SNAPSHOT_FILE.read_bytes()


# --- what gets uploaded, and what never does ---------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # Category 1: tracked and published.
    write(root / "catalog/catalog.json")
    write(root / "catalog/README.md")
    write(root / "catalog/AGENTS.md")
    write(root / "catalog/vector/2025/collection.json")
    write(root / "catalog/vector/2025/thumbnail.png")
    write(root / "catalog/vector/fields-yearly/styles/count.json")
    write(root / "catalog/.portolan/metadata.yaml")  # the one dotfile exception

    # Category 2: tracked, never published.
    write(root / "tools/publish.py")
    write(root / "tests/test_publish.py")
    write(root / "pipeline/stage_global.py")
    write(root / "README.md")
    write(root / "CLAUDE.md")
    write(root / "catalog.publish.yaml")
    write(root / "staging-data/fields-2025.pmtiles")

    # Dotfiles inside the published directory are tracked but not uploaded.
    write(root / "catalog/.portolan/config.yaml")
    write(root / "catalog/.portolan/state.json")
    write(root / "catalog/_assets/.gitkeep", "")

    config = {
        "write_prefix": "s3://a-bucket/ftw/global-data-2e",
        "public_base": "https://data.example.org/ftw/global-data-2e",
        "publish_dir": "catalog",
    }
    uploads = collect_uploads(config, root)
    keys = {u.key for u in uploads}

    expected = {
        "ftw/global-data-2e/catalog.json",
        "ftw/global-data-2e/README.md",
        "ftw/global-data-2e/AGENTS.md",
        "ftw/global-data-2e/vector/2025/collection.json",
        "ftw/global-data-2e/vector/2025/thumbnail.png",
        "ftw/global-data-2e/vector/fields-yearly/styles/count.json",
        "ftw/global-data-2e/.portolan/metadata.yaml",
    }
    check(keys == expected, f"upload set wrong.\n  extra:   {keys - expected}"
                            f"\n  missing: {expected - keys}")

    # The bare-prefix case: no prefix at all.
    flat = dict(config, write_prefix="s3://a-bucket")
    check(
        {u.key for u in collect_uploads(flat, root)}
        == {k.removeprefix("ftw/global-data-2e/") for k in expected},
        "keys are wrong when write_prefix names no prefix",
    )

    # Only the directories the catalog occupies get listed — never a bare
    # recursive sweep of write_prefix, which would walk every COG and parquet
    # sharing it.
    dirs = key_dirs(uploads)
    check(dirs == [
        "ftw/global-data-2e/",
        "ftw/global-data-2e/.portolan/",
        "ftw/global-data-2e/vector/2025/",
        "ftw/global-data-2e/vector/fields-yearly/styles/",
    ], f"listing plan wrong: {dirs}")
    check(
        key_dirs([Upload(root / "x", "rootkey.json", "application/json")])
        == [""],
        "a bucket-root key lists under the empty prefix",
    )

# --- split_s3_uri ------------------------------------------------------
check(split_s3_uri("s3://b/a/c") == ("b", "a/c"), "plain uri")
check(split_s3_uri("s3://b/a/c/") == ("b", "a/c"), "trailing slash")
check(split_s3_uri("s3://b") == ("b", ""), "bare bucket")
check(split_s3_uri("s3://b/") == ("b", ""), "bare bucket, trailing slash")

# --- content types -----------------------------------------------------
check(content_type_for(Path("a/catalog.json")) == "application/json",
      "catalog.json is plain json")
check(content_type_for(Path("a/collection.json")) == "application/json",
      "collection.json is plain json")
check(content_type_for(Path("a/utm31.json")) == "application/geo+json",
      "an item is geo+json")
check(
    content_type_for(Path("a/styles/default.json"))
    == "application/vnd.mapbox.style+json",
    "json under styles/ is a MapLibre style",
)
check(
    content_type_for(Path("a/fields.style.json"))
    == "application/vnd.mapbox.style+json",
    "*.style.json is a MapLibre style",
)
check(
    content_type_for(Path("a/d.parquet")) == "application/vnd.apache.parquet",
    "parquet",
)
check(content_type_for(Path("a/t.pmtiles")) == "application/vnd.pmtiles",
      "pmtiles")
check(content_type_for(Path("a/notes.txt")).startswith("text/plain"),
      "txt serves as plain text")
check(content_type_for(Path("a/README.md")).startswith("text/markdown"),
      "markdown")
check(content_type_for(Path("a/thumbnail.png")) == "image/png", "png")
check(content_type_for(Path("a/x.unknown")) == "application/octet-stream",
      "unknown suffix falls back")

# --- change detection --------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    local = write(Path(tmp) / "f.json", "hello")
    digest = hashlib.md5(b"hello").hexdigest()  # noqa: S324
    upload = Upload(local, "k", "application/json")

    check(is_unchanged(upload, {"k": (5, digest)}), "identical bytes")
    check(is_unchanged(upload, {"k": (5, f'"{digest}"')}), "quoted etag")
    check(not is_unchanged(upload, {}), "absent key")
    check(not is_unchanged(upload, {"other": (5, digest)}), "key mismatch")
    check(not is_unchanged(upload, {"k": (5, "0" * 32)}), "etag differs")
    check(not is_unchanged(upload, {"k": (9, digest)}), "size differs")
    check(not is_unchanged(upload, {"k": (5, "abc-2")}),
          "multipart etag re-uploads catalog metadata")
    check(
        is_unchanged(upload, {"k": (5, "abc-2")},
                     multipart_matches_on_size=True),
        "multipart etag matches on size for data uploads",
    )
    check(
        not is_unchanged(upload, {"k": (9, "abc-2")},
                         multipart_matches_on_size=True),
        "multipart size mismatch still uploads",
    )

# --- the sentinel guard ------------------------------------------------
check(
    unedited_sentinels({
        "write_prefix": "s3://EXAMPLE-BUCKET/EXAMPLE-PREFIX",
        "public_base": "https://example.invalid/EXAMPLE-PREFIX",
    }) != [],
    "an unedited config is refused",
)
check(
    unedited_sentinels({
        "write_prefix": "s3://real/prefix",
        "public_base": "https://data.example.org/prefix",
    }) == [],
    "an edited config is accepted",
)

# --- the retrying upload pool ------------------------------------------
# A fake subprocess.run stands in for the aws CLI, so this stays offline.
class FakeRun:
    """Records aws s3 cp calls; fails a chosen key a chosen number of times."""

    def __init__(self, fail_key: str | None = None, fail_times: int = 99):
        self.calls: list[list[str]] = []
        self.fail_key = fail_key
        self.fail_times = fail_times
        self.failed = 0

    def __call__(self, cmd, capture_output=True, text=True):
        self.calls.append(cmd)
        uri = cmd[4]
        if self.fail_key and uri.endswith(self.fail_key) \
                and self.failed < self.fail_times:
            self.failed += 1
            return subprocess.CompletedProcess(cmd, 1, "", "IncompleteBody")
        return subprocess.CompletedProcess(cmd, 0, "", "")


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    batch = [
        Upload(write(root / f"f{i}.json"), f"p/f{i}.json", "application/json")
        for i in range(30)
    ]

    real_run, real_sleep = publish.subprocess.run, publish.time.sleep
    publish.time.sleep = lambda s: None
    try:
        fake = FakeRun()
        publish.subprocess.run = fake
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            failed = upload_all(batch, "a-bucket", "us-west-2", "aws")
        check(failed == [], "no failures")
        check(
            {c[4] for c in fake.calls}
            == {f"s3://a-bucket/{u.key}" for u in batch},
            "every object is uploaded exactly once",
        )
        check(
            all("--content-type" in c for c in fake.calls),
            "the content type reaches aws s3 cp",
        )
        check(
            len(out.getvalue().splitlines()) < len(batch),
            "progress does not print one line per object",
        )

        # A transient failure retries and succeeds; the run stays green.
        fake = FakeRun(fail_key="p/f7.json", fail_times=2)
        publish.subprocess.run = fake
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            failed = upload_all(batch, "a-bucket", "us-west-2", "aws")
        check(failed == [], f"transient failure retries to success: {failed}")
        check("retry" in out.getvalue(), "the retry is reported")

        # A persistent failure names itself and stops nothing else.
        fake = FakeRun(fail_key="p/f7.json")
        publish.subprocess.run = fake
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            failed = upload_all(batch, "a-bucket", "us-west-2", "aws",
                                retries=1)
        check(failed == ["p/f7.json"], f"the failed key is named: {failed}")
        check("p/f7.json" in err.getvalue(), "the failed key goes to stderr")
    finally:
        publish.subprocess.run, publish.time.sleep = real_run, real_sleep

# --- the non-recursive remote index ------------------------------------
config = {
    "write_prefix": "s3://a-bucket/pre",
    "public_base": "https://x.example.org/pre",
    "publish_dir": "catalog",
    "region": "us-west-2",
}
uploads = [
    Upload(Path("x"), "pre/catalog.json", "application/json"),
    Upload(Path("x"), "pre/vector/2025/collection.json", "application/json"),
]

real_list, real_cli = publish._list_dir, publish.aws_cli
try:
    listed: list[str] = []

    def fake_list(aws, bucket, prefix, region):
        listed.append(prefix)
        return [[f"{prefix}some.json", 5, '"abc"']]

    publish.aws_cli = lambda: "aws"
    publish._list_dir = fake_list
    index = remote_index(uploads, config)
    check(sorted(listed) == ["pre/", "pre/vector/2025/"],
          f"remote_index lists only occupied dirs: {listed}")
    check(index.get("pre/some.json") == (5, "abc"),
          "the index carries (size, etag) with quotes stripped")

    def broken_list(aws, bucket, prefix, region):
        raise subprocess.CalledProcessError(1, "aws", stderr="denied")

    publish._list_dir = broken_list
    out = io.StringIO()
    with redirect_stdout(out):
        index = remote_index(uploads, config)
    check(index == {}, "a failed listing returns {} (everything changed)")
    check("treating every file as changed" in out.getvalue(),
          "the fallback is announced, never silent")

    publish.aws_cli = lambda: None
    out = io.StringIO()
    with redirect_stdout(out):
        index = remote_index(uploads, config)
    check(index == {}, "a missing aws CLI returns {} and still dry-runs")
finally:
    publish._list_dir, publish.aws_cli = real_list, real_cli

# --- --confirm never guesses: failed listings and changed snapshots ----
real_list, real_cli = publish._list_dir, publish.aws_cli
try:
    publish.aws_cli = lambda: "aws"

    def denied(aws, bucket, prefix, region):
        raise subprocess.CalledProcessError(1, "aws", stderr="denied")

    publish._list_dir = denied
    try:
        remote_index(uploads, config, strict=True)
        check(False, "strict remote_index must raise on a failed listing")
    except publish.ListingError as exc:
        check("denied" in str(exc), "the strict error names the failure")

    def half_broken(aws, bucket, prefix, region):
        if prefix.endswith("vector/2025/"):
            raise subprocess.CalledProcessError(1, "aws", stderr="throttled")
        return [[f"{prefix}some.json", 5, '"abc"']]

    publish._list_dir = half_broken
    try:
        remote_index(uploads, config, strict=True)
        check(False, "ANY failed directory listing must abort a strict index")
    except publish.ListingError:
        pass

    publish.aws_cli = lambda: None
    try:
        remote_index(uploads, config, strict=True)
        check(False, "a missing aws CLI must abort a strict index")
    except publish.ListingError:
        pass
finally:
    publish._list_dir, publish.aws_cli = real_list, real_cli

# --- strict on the RECURSIVE path, which is the one a real publish takes ---
# Past RECURSIVE_LISTING_THRESHOLD directories remote_index short-circuits to
# boto3. The real catalog is always over the threshold (516 directories even
# with --skip-generated), so a strict guard that only covered the
# per-directory walk would be dead code in every actual publish.
class _FailingPaginator:
    def paginate(self, **kwargs):
        raise RuntimeError("AccessDenied listing the bucket")
        yield  # pragma: no cover - makes this a generator for shape only


class _FailingClient:
    def get_paginator(self, name):
        return _FailingPaginator()


class _FakeBoto3:
    def __init__(self, client):
        self._client = client

    def client(self, *args, **kwargs):
        if self._client is None:
            raise RuntimeError("NoRegionError: you must specify a region")
        return self._client


wide = [
    Upload(Path("x"), f"pre/raster/2025/zone={n:02d}/catalog.json",
           "application/json")
    for n in range(1, 80)
]
check(len(key_dirs(wide)) > publish.RECURSIVE_LISTING_THRESHOLD,
      "the fixture must exceed RECURSIVE_LISTING_THRESHOLD to reach boto3")
real_boto3 = sys.modules.get("boto3")
try:
    sys.modules["boto3"] = _FakeBoto3(_FailingClient())
    out = io.StringIO()
    with redirect_stdout(out):
        check(remote_index(wide, config) == {},
              "a failed recursive listing still returns {} for a dry run")
    check("treating every file as changed" in out.getvalue(),
          "the recursive fallback is announced, never silent")
    try:
        remote_index(wide, config, strict=True)
        check(False, "a failed recursive listing must raise under strict")
    except publish.ListingError as exc:
        check("AccessDenied" in str(exc),
              "the strict recursive error names the failure")

    # boto3.client() itself can fail (NoRegionError, ProfileNotFound), which
    # is a listing failure like any other.
    sys.modules["boto3"] = _FakeBoto3(None)
    try:
        remote_index(wide, config, strict=True)
        check(False, "a failing boto3 client must raise under strict")
    except publish.ListingError:
        pass

    del sys.modules["boto3"]
    sys.modules["boto3"] = None  # import boto3 -> ImportError
    try:
        remote_index(wide, config, strict=True)
        check(False, "no boto3 and too many dirs must raise under strict")
    except publish.ListingError:
        pass
finally:
    if real_boto3 is None:
        sys.modules.pop("boto3", None)
    else:
        sys.modules["boto3"] = real_boto3

PREFIX = "pre"
snap = {"raster/2025/collection.json": "aaa", "raster/2025/README.md": "bbb"}
changed = [
    Upload(Path("x"), "pre/raster/2025/collection.json", "application/json"),
    Upload(Path("x"), "pre/raster/2025/README.md", "text/markdown"),
    Upload(Path("x"), "pre/raster/2025/new.png", "image/png"),
    Upload(Path("x"), "pre/raster/2024/AGENTS.md", "text/markdown"),
    Upload(Path("x"), "pre/vector/2025/collection.json", "application/json"),
    Upload(Path("x"), "pre/raster/README.md", "text/markdown"),
]
remote = {
    "pre/raster/2025/collection.json": (9, "aaa"),   # still the snapshotted version
    "pre/raster/2025/README.md": (9, '"zzz"'),       # changed in the bucket since
    "pre/raster/2025/new.png": (9, "ccc"),           # exists remotely, never snapshotted
    "pre/vector/2025/collection.json": (9, "zzz"),   # not a raster year file
    "pre/raster/README.md": (9, "zzz"),              # raster tree file, not a year file
}                                                    # raster/2024/AGENTS.md is absent remotely
check(publish.snapshot_conflicts(changed, remote, PREFIX, snap)
      == ["pre/raster/2025/README.md", "pre/raster/2025/new.png"],
      "only changed or unrecorded remote raster year objects conflict")
check(publish.snapshot_conflicts(changed, {}, PREFIX, snap) == [],
      "nothing remote, nothing to overwrite")
check(publish.RASTER_YEAR_FILE.match("raster/2025/x.json")
      and not publish.RASTER_YEAR_FILE.match("raster/2025/zone=15/catalog.json")
      and not publish.RASTER_YEAR_FILE.match("vector/2025/x.json"),
      "the guard covers raster/{year}/ files only")

# main(): drive it with a fake lister and a recording uploader
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    local_a = write(root / "collection.json", "new")
    local_b = write(root / "other.json", "other")
    ups = [
        Upload(local_a, "pre/raster/2025/collection.json", "application/json"),
        Upload(local_b, "pre/vector/2025/collection.json", "application/json"),
    ]
    saved = (publish.load_config, publish.collect_uploads, publish._list_dir,
             publish.aws_cli, publish.upload_all, publish.load_snapshot,
             publish.SNAPSHOT_FILE, sys.argv)
    uploaded: list[list[str]] = []
    snap_file = root / "raster_snapshot.json"

    def run(argv, lister, snapshot):
        uploaded.clear()
        publish.load_config = lambda: dict(config)
        publish.collect_uploads = lambda c: list(ups)
        publish._list_dir = lister
        publish.aws_cli = lambda: "aws"
        publish.load_snapshot = lambda *a: dict(snapshot)
        publish.SNAPSHOT_FILE = snap_file
        publish.upload_all = lambda ch, *a, **k: (uploaded.append(
            [u.key for u in ch]) or [])
        sys.argv = ["publish.py", *argv]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = publish.main()
        return code, out.getvalue(), err.getvalue()

    try:
        def listing(remote_etags):
            def lister(aws, bucket, prefix, region):
                return [[k, 5, f'"{e}"'] for k, e in remote_etags.items()
                        if k.rsplit("/", 1)[0] + "/" == prefix]
            return lister

        code, out, err = run(["--confirm"], denied, {})
        check(code == 1 and not uploaded,
              "--confirm aborts and uploads nothing when a listing fails")
        check("refusing to upload" in err, "the abort says why")

        code, out, err = run([], denied, {})
        check(code == 0 and not uploaded and "2 to upload" in out,
              "a dry run with a failed listing still works (everything changed)")

        code, out, err = run(["--confirm", "--force"], denied, {})
        check(code == 0 and uploaded == [[u.key for u in ups]],
              "--force uploads without listing")

        bucket_state = {"pre/raster/2025/collection.json": "newer-in-bucket"}
        stale_snap = {"raster/2025/collection.json": "snapshot-md5"}
        code, out, err = run(["--confirm"], listing(bucket_state), stale_snap)
        check(code == 1 and "pre/raster/2025/collection.json" in err
              and "SKIPPED" in err,
              "--confirm skips a raster object that changed since the "
              "snapshot and exits non-zero")
        check(uploaded == [["pre/vector/2025/collection.json"]],
              "one conflicting raster file does not block the rest of the "
              f"catalog: {uploaded}")

        code, out, err = run([], listing(bucket_state), stale_snap)
        check(code == 1 and not uploaded and "SKIPPED" in err,
              "the dry run reports the conflict and exits non-zero too")
        check("would upload  pre/raster/2025/collection.json" not in out,
              "the dry-run preview does not promise to upload a skipped key")

        code, out, err = run(["--confirm", "--force"], listing(bucket_state),
                             stale_snap)
        check(code == 0 and uploaded == [[u.key for u in ups]],
              "--force overrides the snapshot guard")

        code, out, err = run(["--confirm"], listing(bucket_state),
                             {"raster/2025/collection.json": "newer-in-bucket"})
        check(code == 0 and uploaded == [[u.key for u in ups]],
              "an object still at its snapshotted version may be overwritten")

        # The guard re-records what it just published, so the next edit of the
        # same file is not mistaken for a third-party publish.
        recorded = json.loads(snap_file.read_text())
        check(recorded.get("raster/2025/collection.json")
              == hashlib.md5(local_a.read_bytes()).hexdigest(),
              f"a successful publish re-records the raster ETag: {recorded}")
        check("re-recorded 1 raster year ETag" in out,
              "the re-record is announced so the operator commits it")
        check("raster/2025/collection.json" in recorded
              and "vector/2025/collection.json" not in recorded,
              "only raster year keys are recorded")
    finally:
        (publish.load_config, publish.collect_uploads, publish._list_dir,
         publish.aws_cli, publish.upload_all, publish.load_snapshot,
         publish.SNAPSHOT_FILE, sys.argv) = saved

# --- the snapshot cannot rot silently ----------------------------------
# The guard can only speak for keys it recorded, so every local
# catalog/raster/{year}/ file must appear in tools/raster_snapshot.json.
snapshot_on_disk = publish.load_snapshot()
local_year_keys = sorted(
    f"raster/{d.name}/{f.name}"
    for d in (ROOT / "catalog" / "raster").iterdir()
    if d.is_dir() and re.fullmatch(r"\d{4}", d.name)
    for f in sorted(d.iterdir()) if f.is_file()
)
check(bool(local_year_keys), "there are raster year files to check")
missing = [k for k in local_year_keys if k not in snapshot_on_disk]
check(not missing,
      "tools/raster_snapshot.json does not record " + ", ".join(missing)
      + " — re-record it with tools/raster_snapshot.py")
check(all(v is None or re.fullmatch(r"[0-9a-f]{32}(-\d+)?", v)
          for v in snapshot_on_disk.values()),
      "every recorded value is an ETag or null (known absent)")

# A truncated snapshot must name the file, not raise a bare JSONDecodeError.
with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "raster_snapshot.json"
    bad.write_text("{not json")
    try:
        publish.load_snapshot(bad)
        check(False, "a truncated snapshot must fail loudly")
    except SystemExit as exc:
        check("raster_snapshot.json" in str(exc),
              "the snapshot error names the file")

# --- raster_snapshot.py: records every local key, refuses a bad listing ---
import raster_snapshot  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    write(root / "catalog/raster/2025/collection.json", "a")
    write(root / "catalog/raster/2025/versions.json", "b")
    write(root / "catalog/raster/README.md", "c")   # not a year file
    out_file = root / "raster_snapshot.json"
    saved = (publish.load_config, publish.collect_uploads,
             publish.remote_index, publish.SNAPSHOT_FILE)
    try:
        publish.load_config = lambda: dict(config)
        publish.collect_uploads = lambda c: collect_uploads(c, root)
        publish.SNAPSHOT_FILE = out_file
        # Only one of the two year files exists in the bucket.
        publish.remote_index = lambda u, c, strict=False: {
            "pre/raster/2025/collection.json": (1, "etag-a"),
        }
        out = io.StringIO()
        with redirect_stdout(out):
            code = raster_snapshot.main()
        written = json.loads(out_file.read_text())
        check(code == 0 and written == {
            "raster/2025/collection.json": "etag-a",
            "raster/2025/versions.json": None,
        }, f"raster_snapshot records every local year key: {written}")

        def boom(u, c, strict=False):
            raise publish.ListingError("denied")

        publish.remote_index = boom
        err = io.StringIO()
        with redirect_stderr(err):
            code = raster_snapshot.main()
        check(code == 1 and "refusing to record" in err.getvalue(),
              "a failed listing is not recorded as an empty snapshot")
    finally:
        (publish.load_config, publish.collect_uploads,
         publish.remote_index, publish.SNAPSHOT_FILE) = saved

check(publish.SNAPSHOT_FILE.read_bytes() == SNAPSHOT_BEFORE,
      "the tests must not rewrite the committed tools/raster_snapshot.json")

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: publish contract holds")
