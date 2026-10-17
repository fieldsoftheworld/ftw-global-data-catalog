"""merge_polygons: the documented invocation, the aux join, resume and reruns."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

PP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PP))

SPEC = importlib.util.spec_from_file_location("merge_polygons", PP / "merge_polygons.py")
mp = importlib.util.module_from_spec(SPEC)
sys.modules["merge_polygons"] = mp
SPEC.loader.exec_module(mp)


def _tile(path: Path, tk: str, n: int, area: float = 1000.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.sql("load spatial")
    con.sql(
        f"""COPY (SELECT '{tk}' AS tile_key, (i + 1)::BIGINT AS parcel_id,
          true AS in_utm_zone, true AS in_mgrs_square, {area} AS area_m2, 0.7 AS pf_mean,
          0.0 AS frac_water,
          ST_MakeEnvelope(3.0 + i*0.001, 51.0, 3.0 + i*0.001 + 0.0005, 51.0005) AS geometry
        FROM range({n}) t(i)) TO '{path}' (FORMAT parquet)"""
    )
    con.close()


def _stamped(cwd: Path, tk: str, n: int = 3, stamp: bytes | None = None) -> None:
    """An input tile carrying ``stamp`` as its ``outline_provenance``, or none at all.

    ``stamp=None`` is an outline written before the stamp existed: a resumed run keeps
    those as current (``fingerprint`` does not cover the stamp), so they are the realistic
    mixed-year case, not a hypothetical one.
    """
    src = cwd / "simplified/2025" / f"{tk}.parquet"
    _tile(src, tk, n)
    if stamp is None:
        return
    t = pq.read_table(src)
    md = dict(t.schema.metadata or {})
    md[mp.OUTLINE_PROVENANCE] = stamp
    pq.write_table(t.replace_schema_metadata(md), src)


def _aux(path: Path, tk: str, n: int, dup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"SELECT '{tk}' AS tile_key, (i+1)::BIGINT AS parcel_id, 0.5 AS patch_sat_mean, "
        f"0.6 AS patch_sat_max, 0.1 AS patch_pb_mean FROM range({n}) t(i)"
    )
    if dup:
        body += f" UNION ALL {body}"
    con = duckdb.connect()
    con.sql(f"COPY ({body}) TO '{path}' (FORMAT parquet)")
    con.close()


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PP / "merge_polygons.py"), "--year", "2025", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _lists(cwd: Path, keep: str, empty: str = "") -> list[str]:
    (cwd / "tiles.txt").write_text(keep)
    (cwd / "empty.txt").write_text(empty)
    return ["--keep-list", "tiles.txt", "--empty-list", "empty.txt"]


def _written(cwd: Path, zone: str = "31") -> Path:
    return cwd / "merged" / "2025" / f"zone={zone}" / "part-0.parquet"


def _summary(cwd: Path) -> dict:
    return json.loads((cwd / "merged" / "2025" / "_summary.json").read_text())


def test_documented_invocation_with_disjoint_lists_works(tmp_path):
    """tiles.txt and empty.txt are disjoint, and simplify carries empty tiles forward.

    So a verified-empty tile always arrives as an input that is not in the keep
    list. `unexpected = have - expected` aborted on exactly that: measured
    "unexpected input tiles: ['31UFT']", exit 1, on the README's own command.
    """
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    _tile(tmp_path / "simplified/2025/31UFT.parquet", "31UFT", 0)
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n", "31UFT\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _written(tmp_path).is_file()


def test_a_tile_in_neither_list_is_still_refused(tmp_path):
    "Control: an unknown input tile is a real error."
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    _tile(tmp_path / "simplified/2025/31ZZZ.parquet", "31ZZZ", 1)
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n"))
    assert r.returncode != 0
    assert "31ZZZ" in r.stdout + r.stderr


def test_classify_guards_agree_about_the_empty_list():
    have, expected, empty = {"A", "B", "C"}, {"A"}, {"B"}
    unexpected, absent = mp.classify(have, expected, empty)
    assert unexpected == {"C"}
    assert absent == set()
    assert mp.find_gaps({"A"}, {"A", "D"}, {"B"}, None) == {"tiles": ["D"]}


def test_allow_missing_aux_merges_with_null_columns(tmp_path):
    """A zone with no aux file must not raise; an empty glob does, not an empty join.

    Measured before: duckdb IOException "No files found that match the pattern
    aux/2025/31*.parquet" even with --allow-missing.
    """
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    (tmp_path / "aux/2025").mkdir(parents=True)
    r = _run(tmp_path, *_lists(tmp_path, "31UFS\n"), "--allow-missing")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "missing aux: 1" in r.stdout
    tbl = pq.read_table(_written(tmp_path))
    for col in mp.AUX_COLUMNS:
        assert col in tbl.column_names
        assert tbl[col].null_count == tbl.num_rows


def test_missing_aux_without_allow_missing_still_fails(tmp_path):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    (tmp_path / "aux/2025").mkdir(parents=True)
    r = _run(tmp_path, *_lists(tmp_path, "31UFS\n"))
    assert r.returncode != 0
    assert "--allow-missing" in r.stdout + r.stderr


def test_over_matching_aux_glob_does_not_duplicate_parcels(tmp_path):
    """`{aux}/{zone}*.parquet` matched a stray rerun file and the join fanned out.

    Measured: 3 parcels in, "3 -> 3 parcels" printed, parcels_out 3 in
    _summary.json, 6 rows over 3 distinct keys written -- duplicate parcel ids in
    the release, which catalog/vector/{year}/AGENTS.md promises cannot happen.
    """
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    _aux(tmp_path / "aux/2025/31UFS.parquet", "31UFS", 3)
    _aux(tmp_path / "aux/2025/31UFS_rerun.parquet", "31UFS", 3)
    r = _run(tmp_path, *_lists(tmp_path, "31UFS\n"), "--allow-missing")
    assert r.returncode == 0, r.stdout + r.stderr
    con = duckdb.connect()
    n, k = con.sql(
        f"select count(*), count(distinct (tile_key, parcel_id)) from '{_written(tmp_path)}'"
    ).fetchone()
    con.close()
    assert (n, k) == (3, 3)
    assert _summary(tmp_path)["zones"]["31"]["parcels_out"] == n


def test_duplicate_keys_inside_one_aux_file_fail_loudly(tmp_path):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    _aux(tmp_path / "aux/2025/31UFS.parquet", "31UFS", 3, dup=True)
    r = _run(tmp_path, *_lists(tmp_path, "31UFS\n"))
    assert r.returncode != 0
    assert "not unique on (tile_key, parcel_id)" in r.stdout + r.stderr


def test_rerun_with_nothing_retained_clears_the_partition(tmp_path):
    """A stale part-0.parquet with no summary entry is a mixed-generation release.

    fiboa_convert.discover_zones still converts the partition, so the previous
    run's parcels would be published alongside the new run's other zones.
    """
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    args = _lists(tmp_path, "31UFS\n")
    assert _run(tmp_path, "--no-aux", *args).returncode == 0
    assert _written(tmp_path).is_file()
    r = _run(tmp_path, "--no-aux", *args, "--max-km2", "0.0000001")
    assert r.returncode != 0
    assert not _written(tmp_path).exists()
    assert not _written(tmp_path).parent.exists()
    # and the summary records the rerun rather than leaving the old one in place
    assert _summary(tmp_path)["zones"]["31"]["parcels_out"] == 0


def test_resume_skips_a_current_zone_and_redoes_a_changed_one(tmp_path):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    args = ["--no-aux", *_lists(tmp_path, "31UFS\n")]
    assert _run(tmp_path, *args).returncode == 0
    assert "current, skipped" in _run(tmp_path, *args).stdout
    # a changed flag is not current
    assert "current, skipped" not in _run(tmp_path, *args, "--max-km2", "9").stdout
    # a regenerated input is not current either
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 5)
    out = _run(tmp_path, *args).stdout
    assert "current, skipped" not in out
    assert pq.ParquetFile(_written(tmp_path)).metadata.num_rows == 5
    assert "current, skipped" in _run(tmp_path, *args).stdout
    assert "current, skipped" not in _run(tmp_path, *args, "--force").stdout


def test_a_fully_resumed_run_succeeds_and_keeps_its_totals(tmp_path):
    "Skipping every zone is success, and must not zero the summary it resumed from."
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    args = ["--no-aux", *_lists(tmp_path, "31UFS\n")]
    assert _run(tmp_path, *args).returncode == 0
    first = _summary(tmp_path)["total"]
    r = _run(tmp_path, *args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "current, skipped" in r.stdout
    s = _summary(tmp_path)
    assert s["total"] == first
    assert s["zones_skipped"] == 1


def test_temp_file_is_pid_scoped_and_not_a_parquet_glob_match(tmp_path):
    "The corruption class main fixed in 7b889a1: a shared *.parquet temp in the tree."
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    assert _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n")).returncode == 0
    part = _written(tmp_path).parent
    assert [p.name for p in part.glob("*.parquet")] == ["part-0.parquet"]
    assert not list(part.glob("*.tmp*"))
    # and no DuckDB spill files anywhere under the published tree
    assert not list((tmp_path / "merged").rglob("*tmp*"))


def test_tmp_dir_inside_out_root_is_refused(tmp_path):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    r = _run(
        tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n"), "--tmp-dir", "merged/spill"
    )
    assert r.returncode != 0
    assert "inside --out-root" in r.stdout + r.stderr


def test_summary_records_allow_missing_and_the_missing_list(tmp_path):
    """An incomplete release must not be byte-indistinguishable from a complete one.

    fiboa_convert reads these keys into determination:details.
    """
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n31UFZ\n"), "--allow-missing")
    assert r.returncode == 0, r.stdout + r.stderr
    s = _summary(tmp_path)
    assert s["allow_missing"] is True
    assert s["missing"]["tiles"] == ["31UFZ"]
    assert s["tiles_expected"] == 2
    assert s["tiles_present"] == 1
    assert "INCOMPLETE" in r.stdout


def test_incomplete_summary_reaches_the_published_metadata(tmp_path):
    import fiboa_convert as fc

    meta = fc.collection_metadata(
        "ftw-s2-2025",
        2025,
        {"max_km2": 5.0, "simplify_tolerance_m": 5.0, "allow_missing": True,
         "missing": {"tiles": ["31UFZ", "31UGA"]}},
    )
    assert "INCOMPLETE" in meta["determination:details"]
    assert "2 missing input tiles" in meta["determination:details"]
    assert "5 m coverage simplification" in meta["determination:details"]
    assert f"interior holes under {fc.MIN_HOLE_M2:g} m2 filled" in meta["determination:details"]
    clean = fc.collection_metadata("ftw-s2-2025", 2025, {"max_km2": 5.0})
    assert "INCOMPLETE" not in clean["determination:details"]


def test_determination_details_names_the_real_band_order():
    import fiboa_convert as fc

    details = fc.collection_metadata("ftw-s2-2025", 2025, {})["determination:details"]
    assert "B04/B03/B02/B08" in details, "PR2 stamps input_bands as B04,B03,B02,B08"
    assert "B02/B03/B04/B08" not in details


def test_simplify_tolerance_is_read_from_the_input_stamp(tmp_path):
    import simplify_polygons as sp

    src = tmp_path / "in.parquet"
    _tile(src, "31UFS", 1)
    dst = tmp_path / "stamped.parquet"
    sp.write_atomic(pq.read_table(src), dst, sp.fingerprint(src, 7.5))
    assert mp.simplify_tolerance([dst]) == 7.5
    assert mp.simplify_tolerance([src]) is None


@pytest.mark.parametrize("corrupt", [b"", b"PAR1 truncated"])
def test_truncated_or_unstamped_output_is_not_current(tmp_path, corrupt):
    _tile(tmp_path / "simplified/2025/31UFS.parquet", "31UFS", 3)
    args = ["--no-aux", *_lists(tmp_path, "31UFS\n")]
    assert _run(tmp_path, *args).returncode == 0
    _written(tmp_path).write_bytes(corrupt)
    assert "current, skipped" not in _run(tmp_path, *args).stdout
    assert pq.ParquetFile(_written(tmp_path)).metadata.num_rows == 3


def test_details_state_the_spec_the_run_used_not_a_hardcoded_one():
    import fiboa_convert as fc

    spec = "nbg-pb-h0.01-t0.3+R35+F10+G2+A900+q1"
    details = fc.collection_metadata("ftw-s2-2025", 2025, {"spec": spec})["determination:details"]
    assert f"({spec})" in details
    unknown = fc.collection_metadata("ftw-s2-2025", 2025, {})["determination:details"]
    assert "+A900" not in unknown, "no method id recorded: say so, do not invent one"
    assert fc.SPEC_UNRECORDED in unknown


def test_merge_summary_carries_the_outline_spec(tmp_path):
    """outlines stamps {spec, backend} in the provenance; merge records it in _summary.json."""
    _stamped(tmp_path, "31UFS", stamp=json.dumps({"spec": "SPEC+q1", "backend": "fast"}).encode())
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    s = _summary(tmp_path)
    assert s["spec"] == "SPEC+q1" and s["specs"] == ["SPEC+q1"]
    assert s["tiles_unrecorded_spec"] == 0
    assert s["provenance"]["backend"] == "fast"


def test_a_mixed_year_states_both_methods_not_the_first_one(tmp_path):
    """Two zones built by different methods: the release says so, in both ids.

    With one spec the `" / ".join` is indistinguishable from "first spec wins", which is
    the behaviour the union exists to prevent -- measured: reducing the join to
    `specs[0]` left the whole suite green.
    """
    import fiboa_convert as fc

    _stamped(tmp_path, "31UFS", stamp=json.dumps({"spec": "SPEC_A"}).encode())
    _stamped(tmp_path, "32ULB", stamp=json.dumps({"spec": "SPEC_B"}).encode())
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n32ULB\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    s = _summary(tmp_path)
    assert s["specs"] == ["SPEC_A", "SPEC_B"]
    assert s["spec"] == "SPEC_A / SPEC_B"
    details = fc.collection_metadata("ftw-s2-2025", 2025, s)["determination:details"]
    assert "SPEC_A / SPEC_B" in details


@pytest.mark.parametrize(
    "stamp",
    [None, b'{"spec": "SPEC_B", "backe', json.dumps({"model_sha256": "deadbeef"}).encode()],
    ids=["unstamped", "truncated", "pre-stamp-payload"],
)
def test_tiles_with_no_readable_spec_are_not_attributed_to_the_stamped_one(tmp_path, stamp):
    """A tile whose method is unknown must not inherit the method of the tiles beside it.

    Resume is keyed on `fingerprint(src, year, core, halo, backend, simplify_m)`, which
    does not cover the stamp, so outlines written before it stay "current" and are never
    restamped. Attributing a whole year to the one id the scan found is exactly the
    plausible-looking single id this stage exists to avoid. The truncated case also guards
    the merge itself: an unguarded `json.loads` aborted it hours in, after some zone
    partitions had been rewritten and before `_summary.json` was.
    """
    import fiboa_convert as fc

    _stamped(tmp_path, "31UFS", stamp=json.dumps({"spec": "SPEC_A"}).encode())
    _stamped(tmp_path, "31UFT", stamp=stamp)
    r = _run(tmp_path, "--no-aux", *_lists(tmp_path, "31UFS\n31UFT\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    s = _summary(tmp_path)
    assert s["specs"] == ["SPEC_A"] and s["tiles_unrecorded_spec"] == 1
    assert s["spec"] == f"SPEC_A / {fc.SPEC_UNRECORDED}"
    details = fc.collection_metadata("ftw-s2-2025", 2025, s)["determination:details"]
    assert f"(SPEC_A / {fc.SPEC_UNRECORDED})" in details


def test_spec_override_states_the_known_method_without_claiming_the_run_recorded_it(tmp_path):
    """`--spec` fills only the unrecorded slot, and says that is where it came from.

    Re-running fiboa_convert over the released merged tree is the natural way to pick up a
    downstream change, and every one of those `_summary.json` files predates the stamp.
    Without this the release replaces a method id the repo knows with a non-claim.
    """
    import fiboa_convert as fc

    known = "nbg-pb-h0.01-t0.3+A900"
    for s in ({}, {"spec": fc.SPEC_UNRECORDED}):
        details = fc.collection_metadata("ftw-s2-2025", 2025, s, known)["determination:details"]
        assert f"({fc.SPEC_SUPPLIED.format(spec=known)})" in details
        assert fc.SPEC_UNRECORDED not in details
    # A recorded id is never overridden; only the unrecorded part of a mixed year is filled.
    mixed = {"spec": f"SPEC_A / {fc.SPEC_UNRECORDED}"}
    details = fc.collection_metadata("ftw-s2-2025", 2025, mixed, known)["determination:details"]
    assert f"SPEC_A / {fc.SPEC_SUPPLIED.format(spec=known)}" in details
    recorded = fc.collection_metadata("ftw-s2-2025", 2025, {"spec": "SPEC_A"}, known)
    assert "(SPEC_A)" in recorded["determination:details"]
