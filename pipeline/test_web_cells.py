"""The pure parts of web_cells.py (the aggregation itself needs DuckDB's a5 extension and gpio)."""

import pytest
import web_cells as wc


def test_r7_cell_is_the_published_area():
    assert wc.cell_km2(7) == pytest.approx(2075.5, abs=0.1)


def test_cells_are_equal_area_and_four_times_finer_per_resolution():
    for res in range(4, 12):
        assert wc.cell_km2(res) == pytest.approx(4 * wc.cell_km2(res + 1))
    assert 60 * wc.cell_km2(1) == pytest.approx(wc.EARTH_KM2)


def test_published_ladder_is_r4_to_r11_and_r7_is_the_first_direct_resolution():
    assert wc.DIRECT_FROM == 7 and wc.MAX_FINEST == 12


def test_final_sql_measures_density_per_1000_km2_and_coverage_in_percent():
    sql = wc.final_sql(8)
    assert f"{wc.cell_km2(8)!r} * 1000" in sql
    assert f"{wc.cell_km2(8) * 1e6!r}" in sql
    assert "AS density" in sql and "AS pct_covered" in sql
