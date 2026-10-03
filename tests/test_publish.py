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

if errors:
    print("\n".join(f"error  {e}" for e in errors))
    raise SystemExit(1)
print("OK: publish contract holds")
