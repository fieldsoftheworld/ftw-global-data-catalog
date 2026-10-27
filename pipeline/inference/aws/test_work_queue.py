from types import SimpleNamespace

import work_queue


def test_todo_lists_the_unit_tiles_without_a_cog(monkeypatch):
    keys = {
        "zone=31/gzd=31U/31UFS_0_0/31UFS_0_0.tif": 0,
        "zone=31/gzd=31U/31UFS_0_0/31UFS_0_0.json": 0,  # a STAC item, not a COG
        "zone=31/gzd=31U/31UFT_0_0/31UFT_0_0.tif.tmp-12": 0,  # a write in progress
    }
    monkeypatch.setattr(work_queue, "_list", lambda s3, b, prefix: keys)
    monkeypatch.setattr(work_queue, "_get", lambda s3, b, k: "2025 31UFS_0_0\n2025 31UFT_0_0")
    a = SimpleNamespace(bucket="b", prefix="infer/", unit="2025_000", outputs="raster")
    assert work_queue._cogs(None, a, "2025") == {"31UFS_0_0"}
    assert work_queue._missing(None, a) == [("2025", "31UFT_0_0")]


def test_todo_of_an_outline_queue_lists_the_tiles_without_an_outline_file(monkeypatch):
    keys = {
        "31UFS_0_0.parquet": 0,
        "31UFT_0_0.parquet.tmp-12": 0,  # a write in progress
        "logs/31UFU_0_0.parquet": 0,  # not at the outline key
    }
    seen = []

    def listing(s3, b, prefix):
        seen.append(prefix)
        return keys

    monkeypatch.setattr(work_queue, "_list", listing)
    monkeypatch.setattr(
        work_queue, "_get", lambda s3, b, k: "2025 31UFS_0_0\n2025 31UFT_0_0\n2025 31UFU_0_0"
    )
    a = SimpleNamespace(bucket="b", prefix="outlines-r2/", unit="2025_000", outputs="outlines")
    assert work_queue._missing(None, a) == [("2025", "31UFT_0_0"), ("2025", "31UFU_0_0")]
    assert seen == ["outlines-r2/outlines/2025/"]
