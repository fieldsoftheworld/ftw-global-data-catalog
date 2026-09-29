# Comment for existing issue #543 (preflight the pass-1 memory floor)

A second datapoint from the FTW **beta** build, one scale down from the
issue's 1.58 B-row case and with a wider property set:

- 134,259,417 features, 42 GB GeoParquet 2.0, 17 exported properties,
  tylertoo 0.11.0 @ 6b9c3ff.
- `tiles --shard coarse --save-plan --profile bounded` on a 192 GiB job:
  completed in 37m44s with MaxRSS 201,321,784 K — riding the cgroup
  ceiling at ~1.4 KiB/row, ~20× the ~50-80 B/row pass-1 model, despite
  `bounded` spilling the pass-2 sinks.

Two implications for the preflight proposed here:

1. The per-row term needs a **measured property-bytes component**, not the
   geometry-calibrated constant (details and a fix plan in the companion
   issue on `SINK_ROW_OVERHEAD_BYTES`).
2. The preflight should compare against **anonymous** RSS, not total —
   see the companion report on bounded-profile MaxRSS accounting; if spill
   page cache counts toward the number being preflighted, the check will
   refuse jobs that would in fact succeed.

Happy to run instrumented reruns of the beta case on our cluster
(`TYLERTOO_PROFILE_JSON` captures) against a branch.
