# bounded profile: coarse-job MaxRSS rides the cgroup ceiling — spill accounting, or a real resident set?

Milestone: v0.8 — trust

## What happened

A `--profile bounded` coarse job — the profile whose whole point is bounded
memory — reported MaxRSS within 1% of its cgroup limit:

```
tylertoo tiles global2025_gp2.parquet coarse-2025.pmtiles \
  --shard coarse --shard-plan shard-plan-2025.json \
  --min-zoom 0 --max-zoom 13 --layer-name fields --profile bounded \
  --row-group-size 100000 --save-plan convert-2025.plan
# input: 134,259,417 features, 42 GB GeoParquet 2.0, 17 exported properties
# tylertoo 0.11.0 @ 6b9c3ff (pre-#541, so full monolithic convert)
# Slurm: 192 GiB job → Elapsed 00:37:44, MaxRSS 201,321,784 K (~192.0 GiB)
```

It completed — nothing was killed — but an operator cannot distinguish
"healthy, page cache did its job" from "one row-group away from OOM". The
pass-1 model says this dataset needs ~10 GiB
(134M × ~50-80 B, `stream.rs:20-25`), and `bounded` always spills the
pass-2 sinks (`SinkBacking::Spill`, `pipeline.rs`), so ~190 GiB resident is
20× the modeled footprint.

## What to determine (and then fix or document)

1. **Are spill reads/writes file-backed pages that count into RSS?** If the
   Arrow IPC spill files (`LevelSink::Spill`/`SpillState`) or the export
   phase's tail-layout reads are mmap-backed, their pages are reclaimable
   page cache that inflates MaxRSS up to the cgroup ceiling while being
   harmless. If that is the story: (a) document it loudly in
   `docs/diving-deeper` (operators size Slurm jobs off MaxRSS), and
   (b) consider `posix_fadvise(DONTNEED)`/`madvise` after sequential
   consumption so the number reflects the true working set.
2. **If the pages are anonymous**, something in the coarse path retains
   ~1.4 KiB/row beyond the models — that is a genuine leak-shaped bug in
   exactly the profile that promises boundedness, and #543's preflight
   would be built on wrong numbers.
3. **Wire the answer into `TYLERTOO_PROFILE_JSON`**: the continuous RSS
   sampling from #576 exists; add a breakdown (anon vs file-backed, e.g.
   from `/proc/self/smaps_rollup`) at phase boundaries so the next report
   like this one is self-diagnosing.

## Reproduction / evidence available

The FTW beta pipeline reproduces this on demand (fieldsoftheworld/
ftw-global-data-catalog, `pipeline/tile_fields.sbatch`, `MODE=coarse`).
A `TYLERTOO_PROFILE_JSON` capture from the next run of the same command can
be attached here on request; the alpha-scale datapoint in #543
(`pass1 scan: 96836 MiB` at 1.58 B rows, OOM at 192 GiB, success at
360 GiB) is the same shape one scale up.

## Verification

- On a fixture large enough to force spilling inside a small cgroup, the
  smaps-based phase report attributes the resident set; file-backed spill
  pages either stop counting after fadvise or are explicitly documented as
  benign.
- The #543 preflight consumes the anon-only figure.
