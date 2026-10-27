import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import context
import qa_index

MIRROR = "tge-labs/sentinel-2-quarterly-cloudless-mosaics/2025/Q1/15TVG_0_0/B04.tif"


def test_the_s3_href_names_the_same_object_in_the_source_coop_bucket():
    t = pa.table({"tile_key": ["15TVG_0_0"], "b04_href": [f"https://data.source.coop/{MIRROR}"]})
    row = qa_index.with_s3_hrefs(t).to_pylist()[0]
    assert row["b04_s3_href"] == f"s3://us-west-2.opendata.source.coop/{MIRROR}"
    path, opts = context._vsis3(row["b04_s3_href"], row["b04_s3_endpoint"])
    assert path == f"/vsis3/us-west-2.opendata.source.coop/{MIRROR}"
    assert opts["AWS_S3_ENDPOINT"] == "https://s3.us-west-2.amazonaws.com"


def test_an_href_off_the_source_coop_host_is_refused():
    t = pa.table({"b04_href": ["https://example.com/B04.tif"]})
    with pytest.raises(ValueError, match="data.source.coop"):
        qa_index.with_s3_hrefs(t)


def test_cli_rewrites_every_index_and_keeps_its_metadata(tmp_path, monkeypatch):
    src, out = tmp_path / "index", tmp_path / "out"
    src.mkdir()
    t = pa.table({"b04_href": [f"https://data.source.coop/{MIRROR}"] * 2})
    pq.write_table(t.replace_schema_metadata({b"geo": b"{}"}), src / "tile_index_2025.parquet")
    pq.write_table(qa_index.with_s3_hrefs(t), src / "tile_index_2024.parquet")  # run twice
    monkeypatch.setattr(sys, "argv", ["qa_index.py", str(src), str(out)])
    qa_index.main()
    for year in (2024, 2025):
        got = pq.read_table(out / f"tile_index_{year}.parquet")
        assert got.column_names == ["b04_href", "b04_s3_href", "b04_s3_endpoint"]
        assert got.num_rows == 2
    assert pq.read_schema(out / "tile_index_2025.parquet").metadata == {b"geo": b"{}"}
