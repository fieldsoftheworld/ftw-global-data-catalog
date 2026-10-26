# Quarterly-mosaic inference

FP32 ONNX inference on 16-band, north-up 10 m UTM stacks. Band order:
Q1–Q4, each B04/B03/B02/B08 (red, green, blue, NIR). Every input must
carry that order: either `Q1_B04`…`Q4_B08` band descriptions or an
`input_bands=Q1,Q2,Q3,Q4 x B04,B03,B02,B08` tag, both of which the
quarterly-mosaic download pipeline writes. A stack claiming neither
is refused rather than assumed — the tag is the durable half,
since GDAL does not always preserve band descriptions. Fill nodata (`window31`),
divide by 3000; bilinear upsample ×4; 1536 px patches (384 px at 10 m), 50% overlap, positive
Hann blending (see "Patch size" below).
The model emits background/field/boundary logits; outputs retain field and
boundary probabilities as uint8 (scale 1/255, offset 0) at 2.5 m in a COG with
overviews 4-64 (10-160 m) and exact per-band `STATISTICS_*` tags (Portolan
PTL-DAT-009).

```sh
uv venv
uv pip install -r pipeline/inference/requirements.txt
.venv/bin/python pipeline/inference/run.py --input-dir stacks/2025 \
  --output-dir staging-data --year 2025 --model model_fp32.onnx
```

Scores land directly in the bucket's own grouped key,
`staging-data/raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif` (the
`GROUPED_PATH` of `tools/build_raster_items.py`), so a finished year uploads
with `tools/upload_data.py` as-is and lands where the catalog's items point —
no relayout or server-side regrouping. `--layout item` writes the older
per-item-folder key `staging-data/raster/{year}/{tile}/{tile}.tif`, which no
catalog references any more; use it only to reproduce an older run. `--year`
names the year in either layout, and `pipeline/postprocessing/outlines.py`
discovers both.

The COG is written in two steps — a tiled GTiff that gets the overviews, then
a COG copy that reuses them — so peak scratch while a tile is in flight is
about twice the final COG (~1.8 GB for a 40,032² two-band tile). The staged
GTiff is removed as soon as the copy returns, and a previous run's
`.stage-*`/`.tmp-*` leftovers are reclaimed before each write, so a
SIGKILLed task does not accumulate them.

## Numerics of the published 2026-10 run

The 2017-2025 COGs in the catalog were made with these settings. The defaults here are the same
except for the patch size and overlap:

| setting | published run | flag to reproduce the earlier release |
|---|---|---|
| mosaic nodata (-32768) | filled by `window31` before scaling (`nodata.py`) | `--nodata-fill none` |
| CUDA TF32 | off (`use_tf32=0`), `cudnn_conv_algo_search=EXHAUSTIVE` | `--tf32` |
| torch / ONNX Runtime stream barrier | on, around every call | `--no-cuda-sync` |
| patch, overlap, batch | 512 px, 25%, 64 | `--patch 512 --overlap 0.25 --batch 64` |
| normalization | 3000 | unchanged |
| test-time augmentation | none | unchanged |

`window31` replaces each nodata pixel with the mean of the same quarter and band over a 31 px
window when at least 30% of the window is valid (tile borders count as invalid), else with the
mean of the other quarters' valid values at that pixel, else with the tile-quarter-band median.
Valid pixels are never touched. Without it the raw -32768 (-10.9 after scaling) reaches the model
and comes out as spurious field probability inside every nodata hole, which is worst in the
sparse 2017-2019 mosaics.

TF32 is ONNX Runtime's default on CUDA. With it on, the output depends on the batch size and on
the cuDNN algorithm the timing search picks: up to 232/255 where raw nodata reaches the model,
and 5-6/255 with the fill. With it off, H100, A100 (80 GB) and the CPU FP32 provider agree within
1/255 on the validation crops (2/255 between the two GPU models), and H100 runs repeat bit for
bit. The cost is about 45% more GPU time per patch.

The barrier fixes a race between two CUDA streams: torch writes the bound input on its stream and
ONNX Runtime's CUDA provider reads it on its own. Without the barrier the first batch of a tile
could read the previous tile's leftover input, and A100s raced on later batches too. `run.py`
records `nodata_fill`, `tf32` and `cuda_sync` in every COG's tags and in the resume fingerprint,
and refuses a CUDA session that did not take the requested provider options.

The published tiles used ONNX Runtime 1.30.0 and torch 2.12.1 (CUDA 13.0, cuDNN 9.20), and model
`unet_balanced_fp32.onnx` with SHA-256
`e12bcdb9f26478273a552a4cbffb9d838dd942cba7451befd5231fae4933282e`. They were made by the
production repository (`global-ftw-2e`, commit `5d01d4b`, not public). Its streaming runner and
self-test now live here as `stream.py` and `selftest.py`, and `requirements.lock` pins the same
library versions. `nodata.py` produces the same fill as the production implementation on int16
input, checked pixel for pixel on random stacks with holes.

Use the model trained for this exact band order and normalization. Model weights
and their model card are released separately; no checkpoint is downloaded here.
The model SHA-256 and source raster tags accompany every output. Resume compares
input size/mtime, model hash and inference settings. Outputs replace atomically.
Use disjoint `--shard` / `--num-shards` assignments for multiple workers.

`--device cuda` (the default) is refused up front when torch sees no CUDA
device, or when ONNX Runtime falls back to CPU while loading the model: the
onnxruntime-gpu wheel advertises `CUDAExecutionProvider` on any Linux host,
driver or not. The provider is part of the resume fingerprint and of every
output's tags, so switching devices recomputes rather than silently
accepting the other device's COGs.

The production path binds CUDA buffers directly to ONNX Runtime and prefetches
one input tile. Full-resolution accumulators plus two prefetched input stacks
require substantial GPU/host RAM (a 10,008² input produces 40,032² scores).
Adjust batch for GPU capacity. CPU mode supports small verification tiles.
No land-cover mask is applied during inference, and nodata is filled rather than masked.

```sh
uv pip install pytest onnx          # onnx is test-only: torch.onnx.export needs it
.venv/bin/python -m pytest -rs pipeline/inference
```

Tests cover patch edges, small tiles, normalized blending, the COG contract,
the band-order contract, resume/provenance, writer cleanup, the window31 fill
(`test_nodata_fill.py`), the order of the CUDA barrier calls, the pinned provider
options and the run settings in the fingerprint and tags, and the model and
device preflights — the last against real ONNX exports. The blending test
traces tensor allocation and fails if an extra tile-sized buffer appears, which
is the difference between fitting on a 24 GB card and not. Run with `-rs`: the
few tests that cannot run without a GPU skip loudly rather than silently.
CUDA throughput and real-checkpoint parity require a GPU and the released model.

## Patch size

The model runs on 1536 px patches at 2.5 m, 384 px of the 10 m stack (3.84 km), at 50% overlap.
`--patch` takes any multiple of 32, and the ONNX export (`ftw-q-unet-efb3.onnx` on Hugging Face,
`fieldsoftheworld/ftw-q`) has a dynamic height and width.
The published run used 512 px patches at 25% overlap.
In low-contrast cropland, such as the Sahel and the Thar desert, the model then scored fields only
near the centre of each 1.28 km patch, leaving a grid of field blobs with background between them.
On FTW-Q validation and test chips, field IoU at 1536 px is higher in every country with full labels
(Brazil 0.675 to 0.700, Spain 0.789 to 0.804, France 0.815 to 0.827).
At 25% overlap whole tiles still showed seams about 13% below the patch centres; 50% removed them.
An H100 takes about 60 s per tile at batch 8, against about 34 s at the published settings.

## Global run from the mirror

`stream.py` reads each tile's 16 band COGs straight from the Source Cooperative mirror into memory,
runs the model, and writes the score COG with `run.write_score` at
`{out-root}/raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif`.
The work list is `tile_index_{year}.parquet` from `pipeline/mosaics/indexes.py`, filtered by
`pipeline/mosaics/keeplist.txt`; a tile runs only with all four quarters.
Every COG records the run in `prov_*` tags (code, model, library versions, provider options and
settings, hashed into `prov_fingerprint`) and per-tile statistics in `qa_*` tags, with a JSON
sidecar in `{out-root}/_qa/{year}/`.
A shard stops if it finds another run's output, unless `--on-stale redo`.
Writing one COG takes about 2 min on 2 CPUs, so `--writers` (default 2) COG writers run beside
the GPU.

```sh
python pipeline/inference/stream.py --year 2025 --index-dir index \
  --onnx ftw-q-unet-efb3.onnx --out-root staging-data --shard 0 --num-shards 64
```

## Golden self-test

`selftest.py` compares a GPU against a CPU FP32 reference on four crops of real tiles and one whole
tile, with the window31 fill as production runs.
It checks GPU against CPU (within 8/255, and 1/255 in practice), repeat runs and a second session
(bit-identical), batch sizes, the top patch row, the seams at patch borders, and that the whole
tile's corner equals the crop.
Run `fetch` and `make-ref` once on a CPU node, then `check` on every machine type before a run.
Unfilled, GPU and CPU differ by up to 99/255 on crops with nodata, so the gates use the fill.

```sh
python pipeline/inference/selftest.py fetch --root golden --onnx ftw-q-unet-efb3.onnx --full-tile
python pipeline/inference/selftest.py make-ref --root golden
python pipeline/inference/selftest.py check --root golden --full-tile
```

## AWS

`aws/launch.sh` runs `stream.py` on EC2 GPU boxes that need 80 GB GPUs (p5.48xlarge H100s or
p4de A100s); smaller GPUs cannot hold a tile.
Each box installs `requirements.lock`, runs the self-test on every GPU and takes no work if one
fails, then claims units of tiles from an S3 queue (`aws/work_queue.py`).
Finished COGs go to `s3://$FTW_AWS_BUCKET/$PREFIX` at the bucket's own keys.
A spot interruption leaves the unit to another box, and finished tiles are not redone.
The bucket, instance profile and subnets come from `FTW_AWS_BUCKET`, `FTW_AWS_ROLE` and
`FTW_AWS_SUBNETS`; the script header lists the rest.

```sh
pipeline/inference/aws/launch.sh setup          # check the bucket and role, upload the golden set
pipeline/inference/aws/launch.sh bundle         # this commit's pipeline code and the indexes
pipeline/inference/aws/launch.sh units 2025     # 256 units of about 57 tiles
pipeline/inference/aws/launch.sh up 2           # two spot p5.48xlarge boxes
pipeline/inference/aws/launch.sh status
```

## Crop mask

`crop_mask.py` runs after `run.py` and zeroes both bands farther than
`--buffer-m` from any io-lulc crop pixel (2017, 2020 or 2024, on a 40 m grid),
for the tiles in `--tile-list` only. It writes to its own `--output-dir` with
the same layout, so a different buffer needs no new inference.

```sh
.venv/bin/python pipeline/inference/crop_mask.py --input-dir scores --output-dir masked \
  --year 2025 --tile-list low_crop_tiles.txt --buffer-m 2000
```
