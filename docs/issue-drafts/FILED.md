# Filed 2026-09-29 (as cholmes)

- tylertoo-1-auto-estimator.md → https://github.com/geoparquet-io/tylertoo/issues/626 (milestone v0.8 — trust)
- tylertoo-2-bounded-rss.md → https://github.com/geoparquet-io/tylertoo/issues/627 (milestone v0.8 — trust)
- tylertoo-543-comment.md → https://github.com/geoparquet-io/tylertoo/issues/543#issuecomment-5888976623
- gpio-1-aggregate-memory.md → https://github.com/geoparquet/geoparquet-io/issues/1179
- gpio-2-a5-antimeridian.md → https://github.com/geoparquet/geoparquet-io/issues/1180
- gpio-3-coverage-metric.md → https://github.com/geoparquet/geoparquet-io/issues/1181

Also relevant, already fixed upstream since our 6b9c3ff build: tylertoo #574
(--plan-only), #595 (--force on every writer, --spill-dir), #541 (cheap
coarse). Rebuild ~/tylertoo-src at main AFTER the current tiling chain
drains (do not swap the binary under queued jobs), then use
`--shard coarse --plan-only` for the 2024 run.
