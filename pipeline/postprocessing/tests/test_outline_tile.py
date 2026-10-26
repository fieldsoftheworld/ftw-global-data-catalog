"""outlines._run_tile end to end on a small synthetic score raster (fbp.run_windowed)."""

import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outlines as ol

X0, Y0 = 500_000.0, 5_500_000.0  # zone 31, band U


def _field(u8, r0, r1, c0, c1):
    u8[0, r0:r1, c0:c1] = 230
    u8[1, r0:r1, c0:c1] = 10
    for rs, cs in ((slice(r0, r0 + 2), slice(c0, c1)), (slice(r1 - 2, r1), slice(c0, c1)),
                   (slice(r0, r1), slice(c0, c0 + 2)), (slice(r0, r1), slice(c1 - 2, c1))):
        u8[0, rs, cs], u8[1, rs, cs] = 10, 240


def test_a_field_across_a_window_seam_is_one_parcel(tmp_path, monkeypatch):
    h, w = 192, 320
    u8 = np.zeros((2, h, w), np.uint8)
    _field(u8, 60, 120, 60, 220)  # 160 px wide across the core edge at 128, halo 32
    _field(u8, 140, 180, 250, 300)  # inside one window
    src = tmp_path / "31UFS_0_0.tif"
    with rasterio.open(src, "w", driver="GTiff", width=w, height=h, count=2, dtype="uint8",
                       crs="EPSG:32631", transform=rasterio.Affine(2.5, 0, X0, 0, -2.5, Y0)) as ds:
        ds.write(u8)
    sh = (h // 12 + 1, w // 12 + 1)
    aux = {"nod": np.zeros((h // 16 + 1, w // 16 + 1), np.uint8),
           "dem": np.full(sh, 50.0, np.float32), "slope": np.full(sh, 2.0, np.float32),
           "water": np.zeros(sh, bool), "crops": np.ones(sh, bool)}
    monkeypatch.setattr(ol, "aux_rasters", lambda *a, **k: aux)
    monkeypatch.setattr(ol, "mgrs_square", lambda tr, H, W: (X0, Y0 - H * 2.5, X0 + W * 2.5, Y0))
    out = tmp_path / "out"
    out.mkdir()
    prof = ol._run_tile(str(src), "31UFS_0_0", 2025, out, tmp_path, 128, 32, 0.0)
    t = pq.read_table(out / "31UFS_0_0.parquet").to_pylist()
    assert prof["parcels"] == 2 and len(t) == 2
    wide = max(t, key=lambda r: r["ext_w_px"])
    assert wide["ext_w_px"] >= 150  # one parcel across the seam, not two halves
    assert wide["touches_window_edge"]
    assert all(r["in_utm_zone"] and r["in_mgrs_square"] and not r["claimed"] for r in t)
    assert all(r["frac_crops_ever"] == 1.0 and r["slope_mean"] == 2.0 for r in t)
    assert sorted(r["parcel_id"] for r in t) == [1, 2]
