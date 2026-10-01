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
boundary probabilities as uint8 (scale 1/255) at 2.5 m in a COG.

```sh
uv venv
uv pip install -r pipeline/inference/requirements.txt
.venv/bin/python pipeline/inference/run.py --input-dir stacks/2025 \
  --output-dir scores/2025 --model model_fp32.onnx
```

Use the model trained for this exact band order and normalization. Model weights
and their model card are released separately; no checkpoint is downloaded here.
The model SHA-256 and source raster tags accompany every output. Resume compares
input size/mtime, model hash and inference settings. Outputs replace atomically.
Use disjoint `--shard` / `--num-shards` assignments for multiple workers.

The production path binds CUDA buffers directly to ONNX Runtime and prefetches
one input tile. Full-resolution accumulators plus two prefetched input stacks
require substantial GPU/host RAM (a 10,008² input produces 40,032² scores).
Adjust batch for GPU capacity. CPU mode supports small verification tiles.
No land-cover or nodata mask is applied during inference.

```sh
uv pip install pytest
.venv/bin/python -m pytest pipeline/inference/test_inference.py
```

Tests cover patch edges, small tiles, normalized blending and the COG contract.
CUDA throughput and real-checkpoint parity require a GPU and the released model.
