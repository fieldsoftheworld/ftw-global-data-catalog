"""The browse overview's embedded-statistics gate (PTL-DAT-009/010).

The overview COG gets its band statistics from the ``gdalinfo -approx_stats``
pass over the source VRT, which writes them to PAM; the COG translate is what
carries them into the output file's own ``GDAL_METADATA`` tag.
``make_overview.verify_band_stats`` is the check that it did, and these tests
are what keep that check honest — a published COG without embedded statistics
violates a MUST and cannot be repaired without rewriting the file.

    python3 -m pytest pipeline/test_make_overview.py -rs

The end-to-end test needs GDAL's command-line tools on PATH (the rails `ftw`
env has them: /u/cholmes/micromamba/envs/ftw/bin) and skips loudly without them.
"""
import shutil
import subprocess

import numpy as np
import pytest
import rasterio
from affine import Affine

from make_overview import STAT_KEYS, missing_band_stats, verify_band_stats

needs_gdal = pytest.mark.skipif(
    shutil.which("gdalinfo") is None or shutil.which("gdal_translate") is None,
    reason="gdalinfo/gdal_translate are not on PATH (the rails `ftw` env has them)",
)


def band(**stats):
    """One ``gdalinfo -json`` band entry carrying ``stats`` in the default domain."""
    return {"band": 1, "metadata": {"": {k: "1" for k in stats}}}


def test_missing_band_stats_reads_the_default_metadata_domain():
    full = {k: "1" for k in STAT_KEYS}
    assert missing_band_stats({"bands": [band(**full), band(**full)]}) == []
    # One band short of the five, and a band with no metadata at all.
    partial = dict(full)
    partial.pop("STATISTICS_STDDEV")
    assert missing_band_stats({"bands": [band(**full), band(**partial)]}) == [2]
    assert missing_band_stats({"bands": [{"band": 1}]}) == [1]
    # Statistics in another domain are not in the GDAL_METADATA tag.
    assert missing_band_stats(
        {"bands": [{"band": 1, "metadata": {"IMAGE_STRUCTURE": full}}]}
    ) == [1]


@needs_gdal
def test_verify_band_stats_rejects_a_cog_whose_statistics_stayed_in_the_sidecar(tmp_path):
    """The real check over real files: stats-less COG out, stats-carrying COG in."""
    src = tmp_path / "src.tif"
    with rasterio.open(
        src, "w", driver="GTiff", height=64, width=64, count=1, dtype="uint8",
        crs="EPSG:3857", transform=Affine(152.87, 0, 0, 0, -152.87, 0),
    ) as ds:
        ds.write(np.arange(64 * 64, dtype=np.uint8).reshape(1, 64, 64))
    bare = tmp_path / "bare.tif"
    subprocess.run(["gdal_translate", "-q", "-of", "COG", str(src), str(bare)], check=True)
    with pytest.raises(SystemExit, match="PTL-DAT-009"):
        verify_band_stats(bare)

    # -stats writes src.tif.aux.xml; the COG translate copies it into the file.
    subprocess.run(["gdalinfo", "-stats", str(src)], check=True, capture_output=True)
    good = tmp_path / "good.tif"
    subprocess.run(["gdal_translate", "-q", "-of", "COG", str(src), str(good)], check=True)
    verify_band_stats(good)  # no SystemExit
