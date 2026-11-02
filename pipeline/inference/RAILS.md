# Continuing production COG inference on RAILS

Use `production/20261102/` for the current AWS-compatible runtime. It preserves
native 384 → model 1536 patches, 50% overlap, batch 8, window31 nodata fill,
normalization 3000, bilinear ×4 upsampling, positive Hann blending, FP32,
TF32 off and CUDA synchronization on. Inputs remain Q1–Q4 × B04/B03/B02/B08.
This produces probabilities, not binary masks or polygons.

## Scope and inputs

Finish one year before starting another: **2022 → 2021 → 2020 → 2019 → 2017**.
AWS owns 2018 and 2023; 2024/2025 inference is already complete.

| Year | Expected tiles | Existing AWS COG + QA pairs | Remaining at handoff |
|---|---:|---:|---:|
| 2022 | 14,578 | 0 | 14,578 |
| 2021 | 14,584 | 0 | 14,584 |
| 2020 | 14,584 | 1,767 | 12,817 |
| 2019 | 14,572 | 952 | 13,620 |
| 2017 | 14,567 | 0 | 14,567 |

Obtain the run's checksummed `handoff.json`, `keep_YEAR.txt`,
`remaining_YEAR.txt`, `existing_YEAR.json`, original `tile_index_YEAR.parquet`,
model, and golden reference directory from the run coordinator. Do not rebuild
indexes from a newer mirror or use the older published keep-list. Reconcile the
existing inventory before launch. Preserve existing COGs and QA; ETags identify
objects but are not SHA256. Freeze the remaining lists and shard count for each
submitted array. Do not use or modify the AWS work queue.

Expected model hash (ONNX followed by external data, if any):
`d14285f91870a79833aac3529650f385afec605ed415acdf2227a02a0a88d036`.
Expected inference code hash:
`876718a6d18bc77cbc8bcb36eefbcd9cf7e06deb8e2173ccd1412414c61d9820`.

## Environment and preflight

Use project storage for code, environment, inputs, logs and durable output.
Check quota/free space first: a full year occupies several TB. Login nodes are
for setup and submission only. Inspect `sinfo`, `squeue`, reservations and the
available allocation before submitting; no jobs are submitted by this handoff.

Use an H100 80 GB GPU per worker. Verify NVIDIA driver **580 or newer** on the
compute node: the pinned wheels use CUDA 13. If incompatible, stop and report;
do not silently change libraries or numerical settings. A100 80 GB is supported
by the code but requires its own golden parity check before use for this run.

```bash
# Run from a clean catalog checkout. Set these to absolute project paths.
export INF="$PWD/pipeline/inference/production/20261102"
export ENV_DIR=/projects/YOUR_PROJECT/ftw-inference-env
export INDEX_DIR=/projects/YOUR_PROJECT/ftw-inputs/index
export HANDOFF_DIR=/projects/YOUR_PROJECT/ftw-inputs/handoff
export GOLDEN_DIR=/projects/YOUR_PROJECT/ftw-inputs/golden
export MODEL="$GOLDEN_DIR/model/ftw-q-unet-efb3.onnx"
export OUT_ROOT=/projects/YOUR_PROJECT/ftw-staging
export LOG_DIR=/projects/YOUR_PROJECT/ftw-logs
(cd "$INF" && sha256sum -c SHA256SUMS)
uv venv --python 3.14 "$ENV_DIR"
uv pip sync --python "$ENV_DIR/bin/python" "$INF/requirements-lock.txt"
mkdir -p "$OUT_ROOT" "$LOG_DIR"
```

The ONNX path must name the actual model file received; verify its combined hash
with `qa.model_sha256`, and verify `qa.code_sha256` against the values above.
Do not fetch an unpinned `latest` model. Avoid loading a CUDA module that shadows
the wheel libraries; the AWS runtime unsets `LD_LIBRARY_PATH` before Python.
Do not overwrite `CUDA_VISIBLE_DEVICES`: Slurm assigns the worker's device.

On an allocated GPU, run the full test before the first tile:

```bash
unset LD_LIBRARY_PATH
# Set AGAINST to the supplied H100 window31 output directory in GOLDEN_DIR/gpu_outputs.
"$ENV_DIR/bin/python" "$INF/selftest.py" check --root "$GOLDEN_DIR" \
  --onnx "$MODEL" --full-tile --against "$AGAINST" \
  --out "$LOG_DIR/golden-${SLURM_JOB_ID}-${SLURM_ARRAY_TASK_ID:-0}"
```

Require exit 0 and a passing `check.json`; retain the log and machine/driver
identity. First do a one-tile smoke with the command below plus `--limit 1`,
using a separate smoke output directory. Confirm two uint8 field/boundary bands,
2.5 m grid, scale 1/255, expected model/code/settings and matching QA sidecar.
Then submit the full array. Do not use `--allow-dirty`, `--on-stale redo`,
`--tf32`, `--no-cuda-sync`, a crop mask or thresholding.

## Slurm worker template

Save outside the checkout, create the log directory before `sbatch`, and replace
the allocation. These are initial resource requests; inspect actual GPU, CPU,
RSS, disk and output rate, then adjust later jobs without changing numerics.
One GPU, eight CPUs and 96 GB host RAM per task leave headroom for three input
prefetches and three COG writers. Keep output on durable storage.

```bash
#!/bin/bash
#SBATCH --job-name=ftw-cogs
#SBATCH --partition=gpu
#SBATCH --account=YOUR_PROJECT
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-task=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=72:00:00
#SBATCH --array=0-7%8
set -euo pipefail
: "${YEAR:?}" "${INF:?}" "${ENV_DIR:?}" "${INDEX_DIR:?}"
: "${HANDOFF_DIR:?}" "${MODEL:?}" "${OUT_ROOT:?}" "${LOG_DIR:?}"
: "${GOLDEN_DIR:?}" "${AGAINST:?}"
case "$YEAR" in 2022|2021|2020|2019|2017) ;; *) exit 2 ;; esac
unset LD_LIBRARY_PATH
(cd "$INF" && sha256sum -c SHA256SUMS)
"$ENV_DIR/bin/python" "$INF/selftest.py" check --root "$GOLDEN_DIR" \
  --onnx "$MODEL" --full-tile --against "$AGAINST" \
  --out "$LOG_DIR/golden-${SLURM_JOB_ID}-${SLURM_ARRAY_TASK_ID}"
"$ENV_DIR/bin/python" -u "$INF/stream.py" --year "$YEAR" \
  --index-dir "$INDEX_DIR" --keep-list "$HANDOFF_DIR/keep_${YEAR}.txt" \
  --tiles-file "$HANDOFF_DIR/remaining_${YEAR}.txt" \
  --onnx "$MODEL" --out-root "$OUT_ROOT" \
  --shard "$SLURM_ARRAY_TASK_ID" --num-shards 8 \
  --patch 1536 --overlap 0.5 --batch 8 --nodata-fill window31 \
  --require-gpu H100 --prefetch 3 --tile-workers 3 --band-workers 16 --writers 3 \
  --on-stale error --stats-file "$LOG_DIR/stats-${YEAR}-${SLURM_ARRAY_TASK_ID}.jsonl"
```

Submit one year only, with exported paths and `AGAINST` set:

```bash
export YEAR=2022
sbatch --export=ALL --output="$LOG_DIR/%A_%a.out" \
  --error="$LOG_DIR/%A_%a.err" /absolute/path/to/ftw-cogs.sbatch
```

Restart failed tasks with the same frozen list and shard count, after confirming
no live owner of the same shard. Matching completed local COGs are skipped.
If a COG exists without its QA sidecar, stop and reconcile that pair; existence
alone is not completion. Preserve temporary evidence from interrupted writes.

## Delivery back to the CPU pipeline

Outputs must retain these relative paths:

- `raster/YEAR/zone=ZZ/gzd=ZZL/TILE/TILE.tif`
- `_qa/YEAR/TILE.json`

Require exact expected **new** tile sets, nonempty matching COG/QA pairs,
consistent run fingerprints and zero unresolved worker failures. Report expected,
existing, newly completed and remaining counts separately. For 2019/2020, merge
the new inventory with the preserved AWS inventory; never overwrite it.

The full-year raster gate still must pass over the combined inventory, including
structural checks, overview/base decoding and size checks. A successful worker
or golden test is not a year gate. Send logs, golden reports, manifests, file
sizes/SHA256 and per-tile QA to the coordinator. AWS continues CPU/RAM
postprocessing. Keep outputs staged until transfer credentials and the approved
transfer destination are ready; do not publish or run vector stages here.
