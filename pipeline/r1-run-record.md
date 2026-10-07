# How the published 2017-2025 data were made (the 2026-10 rerun)

All nine years of the vectors and rasters in this catalog come from one end-to-end rerun, called
r1, of the production pipeline: the `global-ftw-2e` repository (not public), whose checkout
for the run ended at commit `5d01d4b`. Every raster carries the inference run fingerprint
`346f593fedc3a0d8`, a hash of the inference code, the model, the library versions and the
numeric settings; the commit recorded in a raster's tags is the one it was written at,
`96f6d71` to `5d01d4b`, and none of the commits in between touched fingerprinted code. Tooling
written after the run started (hexagon tiles, outline compaction, uploads) came from the same
repository's `main`. This directory holds the same pipeline, written for a clean checkout. This
file says, stage by stage, what r1 ran, where the equivalent is here, and where the two differ,
so that the published files can be reproduced or checked.

| year | rasters | parcels | km² |
|---|---:|---:|---:|
| 2017 | 7,466 | 133,102,683 | 6,804,131 |
| 2018 | 7,466 | 129,911,998 | 6,936,734 |
| 2019 | 7,466 | 129,384,130 | 6,813,144 |
| 2020 | 7,466 | 139,322,555 | 7,030,274 |
| 2021 | 7,466 | 145,101,573 | 7,170,492 |
| 2022 | 7,466 | 147,088,177 | 7,141,706 |
| 2023 | 7,467 | 141,468,617 | 7,325,758 |
| 2024 | 7,467 | 130,526,810 | 7,125,591 |
| 2025 | 7,467 | 146,741,715 | 7,244,864 |

Counts are from a read of the final zone files (54 files a year). 52RGP_0_0 has no mosaic for
2017-2022 and so has no raster in those years.

## Stage by stage

| stage | what r1 ran | here |
|---|---|---|
| 1 mosaics | The CDSE `sentinel-2-global-mosaics` bands, mirrored byte for byte on Source Cooperative (`tge-labs/sentinel-2-quarterly-cloudless-mosaics`). Every band is a 10 m int16 COG with nodata -32768 on one grid per tile. Work list: the catalog's tile set (at least 1% cropland) restricted to tiles with all four quarters. | `mosaics/` downloads from CDSE and stacks 16 bands; the inputs are the same, the transport differs. |
| 2 inference | The 16 bands of a tile are read into memory (no stacks on disk), nodata is filled with `window31`, scaled by 3,000, upsampled 4x, run in 512 px patches at 25% overlap with batch 64, Hann-blended. Model `unet_balanced_fp32.onnx` (SHA-256 beginning `e12bcdb9`, in full in `inference/README.md`), ONNX Runtime 1.30.0 on the CUDA provider with TF32 off, a torch/ONNX Runtime stream barrier around every call, no test-time augmentation. H100 and A100 80 GB GPUs share one fingerprint (the GPU is recorded, not hashed) because with TF32 off they agree within 1/255; every GPU first passes a golden-tile self-test (29 checks, whole tile included). Work unit `{year}_{shard:03d}` = the year's tiles `[i::128]` (about 58 tiles, 30-50 min), claimed dynamically by an H100 array and an A100 array. 2018 ran on two 8-H100 cloud boxes with the same commit and fingerprint (bit-identical to the cluster's H100 on the reference tile). | `inference/run.py`, `nodata.py`, `predict.py` with the same numerics and defaults. The streaming reader, claim loop, self-test and fingerprint are not here. |
| 3 COG | The raw uint8 raster is rewritten in place as a COG: ZSTD 9, predictor 2, 512 px blocks, overviews 4, 8, 16, 32, 64 (average), exact per-band `STATISTICS_*` from a full-resolution histogram, `same_pixels` digest checked against the source. | `inference/run.py` writes the same layout in one step. Provenance tags differ (`prov_*` and `qa_*` in r1). |
| 4 year gate | Per year, before upload: `qa_scores.py` (exit 0 required: missing tiles, tiles without input not on the no-mosaic list, tmp leftovers, malformed or untagged rasters, GPUs other than H100/A100, TF32 on, barrier off, fill other than `window31`, dirty code, 5 km zero runs, a first-batch step on more than 5% of tiles), `check_cogs.py`, `validate_cogs.py` (layout, 5 levels, overviews recomputed exactly, full decode and sha256 for 10% of tiles), then an upload check (every byte's size and ETag, ~200 range-read tiles over all zones). All nine passed. | Not in this directory (`pipeline/rashid_check.sbatch` is the catalog conformance gate, a different check). |
| 5 outlines | `boundaryvote_tiles.py`, BoundaryVote `nbg-pb-h0.01-t0.5+R25+F10+G2+A900+q1`, 8,192 px cores with a 512 px halo, a parcel kept by the window whose core holds its centroid. A tile also keeps the parcels of its own 100 km square that lie in an adjacent zone's strip with no tile of its own (`claimed`, `ownership.py`). 150-tile slices (50 a year) on a high-memory cloud fleet; 13-20 min typical (77 at most) and up to 16 GB a tile per worker. | `postprocessing/outlines.py` (default spec and backend, no claims). It needs an fbp revision that is not published, see below. |
| 6 patch statistics | `patch_saturation.py` writes per-parcel `patch_sat_*` for merge to join. | Not here; the values are not published and no rule uses them. |
| 7 simplify | The Rust port of GEOS 3.13.1 coverage simplification at 5 m, per tile; polygons GEOS reports as coverage-invalid are simplified on their own (Douglas-Peucker, at most 1.2 m). | `postprocessing/simplify_polygons.py` with `coarsen`, one pass over all polygons. |
| 8 merge | Per UTM zone: keep a parcel when `in_utm_zone AND in_mgrs_square AND area_m2 <= 5e6 AND coalesce(frac_water, 0) < 0.7`, Hilbert-sorted; then the cross-zone seam union, which joins a claimed parcel with the overlapping pieces the next zone's tile kept. | `postprocessing/merge_polygons.py` has the filter; claims and cross-zone seams are not here. |
| 9 fiboa | Per zone, range-split (`fiboa_ranges.py`, ~400,000 rows a range): repair, seam unions (tile, window, equator), 20 m² hole fill, 900 m² floor, 5 km² cap after the union, sea rule, Hilbert order, 8,192-row groups, zstd 19, nine columns, `geo` 1.1.0. | `postprocessing/fiboa_convert.py`, same output for the same input (see `postprocessing/README.md` for the one ordering difference). |
| 10 PMTiles | The `pipeline/` tile scripts, 4 shards for seven years and 8 for two. | See `pmtiles.md`. |
| 11 hexagons | `cells-{year}.pmtiles`, r4 to r11, one resolution per zoom. | `web_cells.py`, see `pmtiles.md`. |
| 12 compact outlines | The raw outlines of every year, lossless on the 2.5 m grid. | `postprocessing/compact_outlines.py`. |

## Commands r1 ran that have no equivalent here

```sh
# inference, per year: H100 array and A100 array claim the 128 units
sbatch --partition=gpu      --array=0-11%12 --export=ALL,YEAR=$Y,NSHARDS=128,CLAIM=1 hpc/stream_infer.sbatch
sbatch --partition=gpu_a100 --array=0-7%8   --export=ALL,YEAR=$Y,NSHARDS=128,CLAIM=1 hpc/stream_infer.sbatch
# COG pass, gate, upload
sbatch --array=0-59 --export=ALL,YEAR=$Y hpc/cogify_scores.sbatch
python scripts/qa_scores.py --cog --gpu H100 A100 --fingerprint $FP --history <earlier QA CSVs>
python scripts/check_cogs.py --fingerprint $FP
# outlines, per 150-tile slice
python scripts/boundaryvote_tiles.py --year $Y --tile-list slice.txt --workers 8
# downstream, per year: patch statistics, simplify, merge (with cross-zone seams), fiboa
python scripts/patch_saturation.py --year $Y --workers N --shard 0 --num-shards 1
rust/coverage-simplify/run.sh --year $Y --tile-list all.txt --threads 8 --shard i --num-shards K
python scripts/merge_polygons.py --year $Y
python scripts/fiboa_ranges.py submit --year $Y            # or: local --jobs N on one box
```

## What the published files cannot yet be rebuilt from

- **fbp.** The method id's `+R25`, `+F10`, `+G2` and `+q1` modifiers are in the fbp revision the run
  used, which has not been pushed to `fieldsoftheworld/fbp`; that repository's `main` accepts only
  `l`, `b`, `f`, `m` and `A`. Until it is pushed, stage 5 can only be run by whoever holds that
  revision, and the raw outlines on the bucket (`intermediate/outlines-compact/`) are the way to
  start from stage 6.
- **The model checkpoint** is released separately (see `inference/README.md`).
- **Orphan-strip claims and cross-zone seams** (stages 5 and 8) are not implemented here. Before
  r1 about 3.0-3.4 M parcels (167-174 k km²) a year were dropped at some zone edges because no
  tile kept them (the run's own estimate, in `ownership.py`), and the claim rule exists to keep
  them. A run from this directory leaves them out, and keeps the pieces of a field that crosses
  such a zone edge in both zone files.
- **The land polygons** for the sea rule are the OpenStreetMap `land-polygons-split-4326` snapshot of
  2026-10-04; a later download differs along coasts.

## Known limitations of the published data

These are recorded in the run and left as they are:

- `frac_water` uses io-lulc 2024 for every year, so a reservoir that filled or drained between 2017
  and 2025 is judged by its 2024 state.
- Cross-tile pieces under 900 m² are dropped before the seam union (none seen in five zones of 2025;
  at most about 2,000 fields over the nine years were expected).
- A field wider than about 1.2 km on each side of a same-zone seam stays two overlapping halves (the
  overlap rule needs 10% of the smaller piece, and same-zone rasters overlap by 40-120 m).
- Parcels near the raster edge have no context beyond it: each tile is predicted alone, so pixels
  within about 1 km of an edge see a one-sided receptive field.
- Real paddocks over 5 km² (Australia, Kazakhstan) are dropped with the flipped-patch blobs by the
  area cap.
- Seam unions keep the smallest member id, so per-tile parcel counts near tile edges lean toward
  the tile with the lower key.
- Where most of a quarter's mosaic is nodata (monsoon cloud over South-East Asia), r1 predicts far
  fewer fields than the earlier release, whose fields there were model noise in the holes: in 2025
  the median tile ratio of mean field probability against the release is 0.46 in UTM zone 47 and
  0.90-0.99 in zones 48-51. In every year the tiles below half the release's value sit mostly in
  zones 43-48, and nearly all of them have more than 1% nodata in their input.
- Each year's median tile mean field probability is within about 3% of the earlier release except
  2017 (-10%), where the nodata fill removes the noise the release carried in cloud holes.
