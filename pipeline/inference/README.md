# Quarterly-mosaic inference

FP32 ONNX inference on 16-band, north-up 10 m UTM stacks. Band order:
Q1–Q4, each B04/B03/B02/B08 (red, green, blue, NIR). Every input must
carry that order: either `Q1_B04`…`Q4_B08` band descriptions or an
`input_bands=Q1,Q2,Q3,Q4 x B04,B03,B02,B08` tag, both of which the
quarterly-mosaic download pipeline writes. A stack claiming neither
is refused rather than assumed — the tag is the durable half,
since GDAL does not always preserve band descriptions. Divide by 3000;
bilinear upsample ×4; 512 px patches, 25% overlap, positive Hann blending.
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
No land-cover or nodata mask is applied during inference.

```sh
uv pip install pytest onnx          # onnx is test-only: torch.onnx.export needs it
.venv/bin/python -m pytest -rs pipeline/inference/test_inference.py
```

Tests cover patch edges, small tiles, normalized blending, the COG contract,
the band-order contract, resume/provenance, writer cleanup, and the model and
device preflights — the last against real ONNX exports. The blending test
traces tensor allocation and fails if an extra tile-sized buffer appears, which
is the difference between fitting on a 24 GB card and not. Run with `-rs`: the
few tests that cannot run without a GPU skip loudly rather than silently.
CUDA throughput and real-checkpoint parity require a GPU and the released model.
