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
    a = SimpleNamespace(bucket="b", prefix="infer/", unit="2025_000")
    assert work_queue._cogs(None, a, "2025") == {"31UFS_0_0"}
    assert work_queue._missing(None, a) == [("2025", "31UFT_0_0")]
