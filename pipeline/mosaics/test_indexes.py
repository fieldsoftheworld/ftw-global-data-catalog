import io
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely

import indexes

HERE = Path(__file__).parent
SIZES = {"B02": 11, "B03": 12, "B04": 13, "B08": 14}
SQUARE = shapely.box(2.0, 48.0, 3.0, 49.0)
ANTIMERIDIAN = shapely.MultiPolygon(
    [shapely.box(179.5, 60, 180, 61), shapely.box(-180, 60, -179.5, 61)]
)


def write_manifest(d, year, quarter, tiles, drop=None, key=None):
    rows = []
    for tile in tiles:
        iid = f"Sentinel-2_mosaic_{year}_{quarter}_{tile}"
        for band, size in SIZES.items():
            if (tile, band) == drop:
                continue
            rows.append(
                {
                    "year": year,
                    "quarter": quarter,
                    "item_id": iid,
                    "band": band,
                    "size_bytes": size,
                    "destination_bucket": indexes.BUCKET,
                    "destination_key": key or indexes.destination_key(iid, band),
                }
            )
    pq.write_table(pa.Table.from_pylist(rows), d / f"manifest_{year}_{quarter}.parquet")


@pytest.fixture
def inputs(tmp_path):
    man = tmp_path / "manifests"
    man.mkdir()
    for q in indexes.QUARTERS:
        write_manifest(man, 2025, q, ["31UDQ_0_0", "01WCP_0_0", "99XXX_0_0"])
    fp = tmp_path / "footprints.parquet"
    pq.write_table(
        pa.table(
            {
                "tile_key": ["31UDQ_0_0", "01WCP_0_0"],
                "geometry": [shapely.to_wkb(SQUARE), shapely.to_wkb(ANTIMERIDIAN)],
            }
        ),
        fp,
    )
    return man, fp


def test_destination_key_is_the_mirror_layout():
    key = indexes.destination_key("Sentinel-2_mosaic_2024_Q2_15SVD_0_0", "B02")
    assert key == "sentinel-2-quarterly-cloudless-mosaics/2024/Q2/15SVD_0_0/B02.tif"
    for iid, band in (
        ("Sentinel-2_mosaic_2024_Q2_15SVD", "B02"),
        ("Sentinel-2_mosaic_2024_Q2_15SVD_0_0", "B05"),
    ):
        with pytest.raises(ValueError, match="unexpected"):
            indexes.destination_key(iid, band)


def test_tile_index_rows_hrefs_and_footprints(inputs):
    t = indexes.tile_index(2025, *inputs)
    assert t.num_rows == 8  # 2 tiles x 4 quarters; the tile without a footprint is dropped
    rows = t.to_pylist()
    assert {r["tile_key"] for r in rows} == {"31UDQ_0_0", "01WCP_0_0"}
    r = next(r for r in rows if r["tile_key"] == "31UDQ_0_0" and r["quarter"] == "Q3")
    assert r["b04_href"] == f"{indexes.MIRROR}/2025/Q3/31UDQ_0_0/B04.tif"
    assert (r["b02_size"], r["b08_size"]) == (11, 14)
    assert (r["xmin"], r["ymin"], r["xmax"], r["ymax"]) == SQUARE.bounds
    assert r["start_datetime"].isoformat() == "2025-07-01T00:00:00+00:00"
    assert r["end_datetime"].isoformat() == "2025-09-30T23:59:59+00:00"
    am = next(r for r in rows if r["tile_key"] == "01WCP_0_0")
    assert shapely.equals(shapely.from_wkb(am["geometry"]), ANTIMERIDIAN)
    assert am["xmax"] - am["xmin"] > 180
    geo = json.loads(t.schema.metadata[b"geo"])
    assert geo["columns"]["geometry"]["geometry_types"] == ["Polygon", "MultiPolygon"]


def test_stac_items_feed_cogify_source_items(inputs, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    t = indexes.stac_items(2025, *inputs)
    path = tmp_path / "items_2025.parquet"
    indexes._write(t, path)
    got = dict(
        duckdb.sql(
            f"select tile_key, list(id order by quarter) from '{path}' group by 1"
        ).fetchall()
    )
    assert got["31UDQ_0_0"] == [f"Sentinel-2_mosaic_2025_{q}_31UDQ_0_0" for q in indexes.QUARTERS]
    item = next(r for r in t.to_pylist() if r["id"].endswith("Q1_01WCP_0_0"))
    assert item["proj:code"] == "EPSG:32601"
    assert item["assets"]["B08"]["file:size"] == 14
    assert item["assets"]["B08"]["alternate"]["s3"]["href"].startswith("s3://tge-labs/")
    assert json.loads(t.schema.metadata[b"stac-geoparquet"]) == {"version": "1.0.0"}


def test_manifest_key_drift_fails(tmp_path, inputs):
    man, fp = inputs
    write_manifest(man, 2025, "Q2", ["31UDQ_0_0"], key="elsewhere/B02.tif")
    with pytest.raises(ValueError, match="is not at"):
        indexes.tile_index(2025, man, fp)


def test_item_missing_a_band_fails(tmp_path, inputs):
    man, fp = inputs
    write_manifest(man, 2025, "Q4", ["31UDQ_0_0"], drop=("31UDQ_0_0", "B03"))
    with pytest.raises(ValueError, match="lack a band"):
        indexes.tile_index(2025, man, fp)


def test_hilbert_order_is_a_permutation_that_groups_neighbours():
    x = np.array([0.0, 100.0, 0.1, 100.1])
    y = np.array([0.0, 0.0, 0.1, 0.1])
    order = indexes.hilbert_order(x, y)
    assert sorted(order) == [0, 1, 2, 3]
    pos = {int(i): k for k, i in enumerate(order)}
    assert abs(pos[0] - pos[2]) == 1 and abs(pos[1] - pos[3]) == 1


def test_fetch_manifests_skips_existing_and_rejects_empty(tmp_path, monkeypatch):
    good = io.BytesIO()
    pq.write_table(pa.table({"x": [1]}), good)
    empty = io.BytesIO()
    pq.write_table(pa.table({"x": pa.array([], pa.int64())}), empty)
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        calls.append(req.full_url)
        assert req.get_header("User-agent") == indexes.USER_AGENT
        return Resp((empty if "Q4" in req.full_url else good).getvalue())

    monkeypatch.setattr(indexes.urllib.request, "urlopen", urlopen)
    (tmp_path / "manifest_2025_Q1.parquet").write_bytes(good.getvalue())
    with pytest.raises(ValueError, match="empty manifest"):
        indexes.fetch_manifests(tmp_path, years=[2025])
    assert len(calls) == 3  # Q1 already present
    assert not (tmp_path / "manifest_2025_Q4.parquet").exists()
    assert not list(tmp_path.glob(".*tmp*"))


def test_keeplist_is_the_2e_tiles_plus_the_new_ones():
    keep = (HERE / "keeplist.txt").read_text().split()
    old = set(
        pq.read_table(HERE / "keeplist_2e.parquet", columns=["tile_key"])["tile_key"].to_pylist()
    )
    new = set((HERE / "keeplist_new.txt").read_text().split())
    assert keep == sorted(old | new)
    assert len(keep) == 14_608
