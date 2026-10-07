import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compact_verify as cv


def _tiles(d: Path, names: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / f"{n}.parquet").write_bytes(n.encode() * 10)


def test_manifest_matches_raw(tmp_path):
    _tiles(tmp_path, ["15SVD_0_0", "31UFU_0_0"])
    m = cv.build_manifest("2025", {"15SVD_0_0", "31UFU_0_0"}, ["50SQJ_0_0"], tmp_path)
    assert m["tiles"] == 2
    assert m["empty"] == 1
    assert m["files"]["15SVD_0_0"]["size"] == 90
    assert cv.check_manifest(m, tmp_path) == []


def test_missing_and_extra_tiles_fail(tmp_path):
    _tiles(tmp_path, ["15SVD_0_0", "99XXX_0_0"])
    with pytest.raises(ValueError, match=r"missing .*31UFU_0_0.*extra .*99XXX_0_0"):
        cv.build_manifest("2025", {"15SVD_0_0", "31UFU_0_0"}, [], tmp_path)


def test_check_detects_changed_and_missing_file(tmp_path):
    _tiles(tmp_path, ["15SVD_0_0", "31UFU_0_0"])
    m = cv.build_manifest("2025", {"15SVD_0_0", "31UFU_0_0"}, [], tmp_path)
    (tmp_path / "15SVD_0_0.parquet").write_bytes(b"x" * 90)  # same size, other bytes
    (tmp_path / "31UFU_0_0.parquet").unlink()
    assert sorted(cv.check_manifest(m, tmp_path)) == ["differs 15SVD_0_0", "missing 31UFU_0_0"]


def test_cli_writes_manifest_and_empty_list(tmp_path, monkeypatch, capsys):
    _tiles(tmp_path / "c", ["15SVD_0_0"])
    (tmp_path / "raw.txt").write_text("out/2025/15SVD_0_0.parquet\n")
    (tmp_path / "empty.txt").write_text("out/2025/_empty/50SQJ_0_0\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x",
            "2025",
            "--raw-list",
            str(tmp_path / "raw.txt"),
            "--empty-list",
            str(tmp_path / "empty.txt"),
            "--compact-dir",
            str(tmp_path / "c"),
            "--manifest",
            str(tmp_path / "m.json"),
        ],
    )
    cv.main()
    assert (tmp_path / "c/_empty.txt").read_text() == "50SQJ_0_0\n"
    assert json.loads((tmp_path / "m.json").read_text())["tiles"] == 1
