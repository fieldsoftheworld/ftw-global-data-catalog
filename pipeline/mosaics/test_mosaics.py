import json
import re

import numpy as np
import pytest
import rasterio
from affine import Affine
from pathlib import Path
from types import SimpleNamespace

import download
import stac
from download import bbox_error, index_row, is_current, shard_index_path, stack_bands


def write_bands(tmp_path, count, value=None):
    paths = []
    for i in range(count):
        path = tmp_path / f"band{i}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=40,
            height=30,
            count=1,
            dtype="int16",
            crs="EPSG:32631",
            transform=Affine(10, 0, 500000, 0, -10, 1000000),
        ) as ds:
            ds.write(np.full((30, 40), i if value is None else value, np.int16), 1)
        paths.append(path)
    return paths


def test_band_order_and_grid_validation(tmp_path):
    paths = write_bands(tmp_path, 16)
    dst = tmp_path / "stack.tif"
    stack_bands(paths, dst, {"source_items": json.dumps(["Q1", "Q2", "Q3", "Q4"])})
    with rasterio.open(dst) as ds:
        assert ds.count == 16 and ds.descriptions[0] == "Q1_B04"
        assert ds.descriptions[-1] == "Q4_B08"
        np.testing.assert_array_equal(ds.read()[:, 0, 0], np.arange(16))
    with rasterio.open(paths[-1], "r+") as ds:
        ds.transform = Affine(10, 0, 500010, 0, -10, 1000000)
    with pytest.raises(ValueError, match="one grid"):
        stack_bands(paths, tmp_path / "bad.tif", {})


def test_band_descriptions_match_band_keys(tmp_path):
    dst = tmp_path / "stack.tif"
    stack_bands(write_bands(tmp_path, 16), dst, {"input_bands": ",".join(download.BAND_KEYS)})
    with rasterio.open(dst) as ds:
        assert list(ds.descriptions) == list(download.BAND_KEYS)
        assert ds.tags()["input_bands"].split(",") == list(download.BAND_KEYS)


def test_wrong_band_count_rejected_and_leaves_no_temp(tmp_path):
    with pytest.raises(ValueError, match="expected 16 band paths, got 15"):
        stack_bands(write_bands(tmp_path, 15), tmp_path / "stack.tif", {})
    assert not list(tmp_path.glob("*.tmp-*"))


def test_temp_removed_when_the_write_fails(tmp_path, monkeypatch):
    paths = write_bands(tmp_path, 16)

    def boom(*args, **kwargs):
        raise RuntimeError("write failed")

    # Only download.py's own np reference, so the fixtures above stay usable.
    monkeypatch.setattr(download, "np", SimpleNamespace(stack=boom))
    with pytest.raises(RuntimeError, match="write failed"):
        stack_bands(paths, tmp_path / "stack.tif", {})
    assert not list(tmp_path.glob("*.tmp-*"))
    assert not (tmp_path / "stack.tif").exists()


def test_is_current_rebuilds_an_unreadable_stack(tmp_path):
    good = tmp_path / "good.tif"
    stack_bands(write_bands(tmp_path, 16), good, {"source_items": "sig"})
    assert is_current(good, "sig")
    assert not is_current(good, "other-sig")
    junk = tmp_path / "junk.tif"
    junk.write_bytes(b"not a tiff")
    assert not is_current(junk, "sig")


@pytest.mark.parametrize(
    "bbox,match",
    [
        ([5.1, 51.0, 5.0, 51.1], "W < E"),
        ([5.0, 51.1, 5.1, 51.0], "S < N"),
        ([5.0, 51.0, 5.1, 51.0], "S < N"),
        ([5.0, 51.0, 5.0, 51.1], "zero-width"),
        ([5.0, -91.0, 5.1, 51.0], r"\[-90, 90\]"),
        ([-181.0, 51.0, 5.1, 51.1], r"\[-180, 180\]"),
    ],
)
def test_bbox_error_rejects_unusable_boxes(bbox, match):
    problem = bbox_error(bbox, allow_antimeridian=False)
    assert problem and re.search(match, problem)


def test_bbox_error_accepts_valid_and_flagged_antimeridian():
    assert bbox_error([5.0, 51.0, 5.1, 51.1], allow_antimeridian=False) is None
    assert bbox_error([-180.0, -90.0, 180.0, 90.0], allow_antimeridian=False) is None
    assert bbox_error([179.0, 51.0, -179.0, 51.1], allow_antimeridian=False)
    assert bbox_error([179.0, 51.0, -179.0, 51.1], allow_antimeridian=True) is None
    # the flag widens W > E only; a zero-width box is still unusable
    assert bbox_error([5.0, 51.0, 5.0, 51.1], allow_antimeridian=True)


def mosaic_item(quarter="Q1", https="https://x/Nodes(B04.tif)/$value"):
    return stac.MosaicItem(
        item_id=f"S2_10m_mosaic_{quarter}_31UFS_0_0",
        year=2025,
        quarter=quarter,
        bbox=(5, 51, 5.1, 51.1),
        assets={
            "B04": stac.BandAsset(
                band="B04",
                s3_bucket="eodata",
                s3_key="Sentinel-2/MSI/MOSAIC/x/B04.tif",
                https_href=https,
                size_bytes=1,
            )
        },
    )


def test_index_row_carries_the_openable_s3_form(monkeypatch):
    monkeypatch.delenv("EODATA_S3_ENDPOINT", raising=False)
    row = index_row("31UFS_0_0", mosaic_item())
    assert row["b04_s3_href"] == "s3://eodata/Sentinel-2/MSI/MOSAIC/x/B04.tif"
    assert row["b04_s3_endpoint"] == download.EODATA_S3_ENDPOINT
    # The OData alternate is kept, but in its own column: it is not openable.
    assert row["b04_odata_href"].endswith("/$value")
    assert "$value" not in row["b04_s3_href"]
    monkeypatch.setenv("EODATA_S3_ENDPOINT", "https://mirror.example/s3")
    assert index_row("t", mosaic_item())["b04_s3_endpoint"] == "https://mirror.example/s3"


def test_index_row_without_an_odata_alternate_still_works():
    row = index_row("31UFS_0_0", mosaic_item(https=""))
    assert row["b04_s3_href"].startswith("s3://eodata/") and row["b04_odata_href"] == ""


def test_index_row_rejects_an_asset_with_no_s3_location():
    item = mosaic_item()
    item.assets["B04"].s3_key = ""
    with pytest.raises(ValueError, match="no S3 location"):
        index_row("31UFS_0_0", item)


def test_shard_index_paths_are_disjoint():
    path = Path("index/tile_index_2025.parquet")
    assert shard_index_path(path, 0, 1) == path
    sharded = [shard_index_path(path, k, 3) for k in range(3)]
    assert len(set(sharded)) == 3
    assert sharded[0] == Path("index/tile_index_2025.shard-0-of-3.parquet")
    assert all(p.suffix == ".parquet" and p.parent == path.parent for p in sharded)


def feature(i, quarter="Q1"):
    return {
        "id": f"S2_10m_mosaic_{quarter}_31UFS_{i}_0",
        "bbox": [5, 51, 5.1, 51.1],
        "assets": {
            band: {
                "href": f"s3://eodata/{band}_{i}.tif",
                "file:size": 1,
                "alternate": {"https": {"href": f"https://eodata/{band}_{i}.tif"}},
            }
            for band in stac.DEFAULT_BANDS
        },
    }


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    "Serves canned pages and records every request, like a STAC API would."

    def __init__(self, pages, cap=20):
        self.pages = pages
        self.calls = []
        self.cap = cap

    def _page(self, method, url, body):
        self.calls.append((method, url, json.dumps(body, sort_keys=True) if body else None))
        if len(self.calls) > self.cap:
            raise AssertionError(f"runaway pagination: {len(self.calls)} requests")
        return Response(self.pages[min(len(self.calls) - 1, len(self.pages) - 1)])

    def post(self, url, json=None, timeout=None):
        return self._page("POST", url, json)

    def get(self, url, timeout=None):
        return self._page("GET", url, None)


def test_pagination_follows_href_and_method():
    url = stac.STAC_SEARCH_URL
    sess = FakeSession([
        {
            "numberMatched": 3,
            "features": [feature(0)],
            "links": [{"rel": "next", "href": url + "?token=p2", "method": "GET"}],
        },
        {
            "numberMatched": 3,
            "features": [feature(1), feature(2)],
            "links": [],
        },
    ])
    items = stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=sess)
    assert len(items) == 3
    assert [c[0] for c in sess.calls] == ["POST", "GET"]
    assert sess.calls[1][1] == url + "?token=p2"


def test_pagination_merges_post_bodies_only_when_asked():
    url = stac.STAC_SEARCH_URL
    merged = FakeSession([
        {
            "features": [feature(0)],
            "links": [
                {
                    "rel": "next",
                    "href": url,
                    "method": "POST",
                    "merge": True,
                    "body": {"token": "p2"},
                }
            ],
        },
        {"features": [feature(1)], "links": []},
    ])
    stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=merged)
    second = json.loads(merged.calls[1][2])
    assert second["token"] == "p2" and second["collections"] == [stac.MOSAICS_COLLECTION]

    replaced = FakeSession([
        {
            "features": [feature(0)],
            "links": [
                {"rel": "next", "href": url, "method": "POST", "body": {"token": "p2"}}
            ],
        },
        {"features": [feature(1)], "links": []},
    ])
    stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=replaced)
    assert json.loads(replaced.calls[1][2]) == {"token": "p2"}


def test_pagination_stops_on_a_non_advancing_cursor():
    url = stac.STAC_SEARCH_URL
    # The CDSE shape that looped: page 2's next link carries no body token, so
    # the previous token stayed merged in and the identical page was re-fetched.
    sess = FakeSession([
        {
            "features": [feature(0)],
            "links": [
                {
                    "rel": "next",
                    "href": url,
                    "method": "POST",
                    "merge": True,
                    "body": {"token": "p2"},
                }
            ],
        },
        {
            "features": [feature(1)],
            "links": [{"rel": "next", "href": url + "?token=p3", "method": "GET"}],
        },
    ])
    with pytest.raises(RuntimeError, match="not advancing"):
        stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=sess)
    assert len(sess.calls) <= 4


@pytest.mark.parametrize("key", ["numberMatched", "context"])
def test_short_result_fails_loudly(key):
    reported = {"numberMatched": 3} if key == "numberMatched" else {"context": {"matched": 3}}
    sess = FakeSession([{**reported, "features": [feature(0)], "links": []}])
    with pytest.raises(RuntimeError, match="incomplete"):
        stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=sess)


def test_single_page_without_a_next_link_is_complete():
    sess = FakeSession([{"numberMatched": 1, "features": [feature(0)], "links": []}])
    items = stac.search_items((5, 51, 5.1, 51.1), 2025, ("Q1",), session=sess)
    assert len(items) == 1 and len(sess.calls) == 1


def test_one_s3_client_per_thread(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("EODATA_S3_ACCESS_KEY", "k")
    monkeypatch.setenv("EODATA_S3_SECRET_KEY", "s")
    built = []

    class Body:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_chunks(self, size):
            yield b"0123456789"

    class Client:
        def get_object(self, Bucket, Key):
            return {"Body": Body(), "ContentLength": 10}

    class Session:
        def client(self, *args, **kwargs):
            built.append(kwargs.get("endpoint_url"))
            return Client()

    monkeypatch.setattr(download.boto3, "Session", lambda *a, **k: Session())
    monkeypatch.setattr(download, "_local", download.threading.local())
    monkeypatch.setattr(
        download.boto3, "client", lambda *a, **k: pytest.fail("used the shared default session")
    )
    asset = type("Asset", (), {"s3_bucket": "b", "s3_key": "k", "size_bytes": 10})()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda i: download.download_asset(asset, tmp_path / f"{i}.bin"), range(8)))
    assert 0 < len(built) <= 2
