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
boundary probabilities as uint8 (scale 1/255, offset 0) at 2.5 m in a COG.

Output COGs match the released 2e tiles: 512 px blocks, ZSTD level 9 with predictor 2,
overviews at 4, 8, 16, 32, 64 (10-160 m; there is no 5 m level, which would add ~40% to the
file size for a resolution the 10 m inputs lack), and the tags `model`, `quantization`
(`uint8 = p*255`), `zstd_level` and `tile_key` beside the model hash and the input stack's
own tags. They are written in two steps (tiled GTiff, overviews, then the COG driver reusing
them) because the COG driver alone would build levels 2-128. `--layout hive` writes the
published `raster/` layout, `{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.tif`, from the stack's
`year` tag and file name; the default `flat` writes `{output-dir}/{tile}.tif`.

```sh
uv venv
uv pip install -r pipeline/inference/requirements.txt
.venv/bin/python pipeline/inference/run.py --input-dir stacks/2025 \
  --output-dir scores/2025 --model model_fp32.onnx
```

This path reads the 16-band stacks that `pipeline/mosaics` builds from CDSE. The 2e release was
produced by streaming the same 16 bands from the byte-faithful Source Cooperative mirror of the
mosaics (`tge-labs/sentinel-2-quarterly-cloudless-mosaics`), tile by tile, in
[global-ftw-2e](https://github.com/taylor-geospatial/global-ftw-2e)'s `scripts/stream_infer.py`; its
model, normalization, blending and output contract are the ones here. The mirror reader and its
per-year tile list (`tile_index_{year}.parquet` filtered by a keep-list) are not part of this
repository. The `source_collection`/`source_href_prefix` tags therefore name the mirror on
released tiles and CDSE on tiles written by this script.

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
