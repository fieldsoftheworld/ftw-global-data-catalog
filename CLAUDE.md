# ftw-global-data-catalog — developer guide

Git-backed Portolan/STAC catalog for the Fields of the World (FTW) Global **2nd Edition** (2e) release (bucket prefix `global-data-2e`).
The build plan is in [docs/plan.md](docs/plan.md) — read it first.

## Models
The main session runs on Fable (`.claude/settings.json`); subagents default to Opus
(`CLAUDE_CODE_SUBAGENT_MODEL`). Use `model: "sonnet"` explicitly for lightweight
exploration/search subagents; keep Opus (the default) for implementation and review subagents.

## Clean publish-directory model
`catalog/` **is** the published catalog — synced 1:1 to Source Cooperative. Everything in
`catalog/` is published; everything outside it never is. Data files are never placed in
`catalog/`, so they cannot be published by accident.

- Write target (uploads): `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/`
- Public href base (all STAC hrefs): `https://data.source.coop/ftw/global-data-2e/`
- Config lives in `catalog.publish.yaml`; this catalog uses `tools/publish.py`
  (stateless size+MD5 change detection), not `portolan push`.

## Layout
- `catalog/` — the published catalog (STAC JSON, README.md, AGENTS.md,
  thumbnails, styles; llms.txt was removed by user ruling 2026-10-01).
  Dotfiles are not published. `catalog/.portolan/` was removed
  on 2026-10-03 (portolan-cli state this catalog does not use; every fact in
  `metadata.yaml` was already in the STAC). `publish.py` still allows
  `.portolan/metadata.yaml` through as a general rule. **The bucket copy was not
  deleted** — publishing never deletes — so ask Chris before removing
  `.portolan/` objects from the bucket.
- Bucket data layouts (both per-item folders, data beside metadata):
  `vector/{year}/zone=NN/utm{NN}.parquet` and
  `raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif` (+ `{tile}.json`,
  `{tile}.thumb.png`; per-year `overview.tif`/`thumbnail.webp`/`items.parquet`
  at `raster/{year}/`; user ruling 2026-10-02). `{ZZ}`/`{GZD}` come from the
  tile key: `01KFS_0_0` → `zone=01/gzd=01K/`. The inference pipeline emits
  the raster hierarchy directly — new generations never need a relayout.
- The raster item tree (67,197 items + 3,690 zone/GZD catalogs) is
  **generated, not committed** (user ruling 2026-10-03; gitignored as
  `catalog/raster/*/zone=*/`). Rebuild it with `tools/build_raster_items.py`,
  in the order `items` → `collections` → `mirror`; `tools/publish.py` walks
  the filesystem, so what publishes is unchanged. A checkout without the
  tree skips the conformance gate in CI and fails it locally — the full
  check runs pre-publish via `pipeline/rashid_check.sbatch` (see
  docs/conformance.md, "generated item tree", and rashid#205).
- `tools/` — `publish.py` (metadata, 1:1), `upload_data.py` (staged data, suffix
  allow-list, never deletes), `build_vector_items.py` / `build_raster_items.py`
  (tree generators), `render_thumbnails.py` (vector thumbnails via chiitiler),
  `make_thumbnails.py` (COG thumbnail core).
- `pipeline/` — all five processing stages (mosaics, inference, postprocessing,
  rails Slurm PMTiles, catalog). `pipeline/README.md` is the end-to-end overview;
  cluster gotchas and measured timings live there too.
- `staging-data/` — gitignored staging tree read by `upload_data.py`; keys mirror its
  layout under the write prefix. `checksums/tiles_meta.json` carries the
  size+multihash for each year's `pmtiles`, `cells` and `mirror` asset, and
  `--checksums` supplies the per-zone sidecar. Both can be reconstructed from the
  committed collection/item JSON if the staging tree is missing — without them a
  regeneration silently drops `file:size`/`file:checksum` and the whole styles
  subtree.
- `tests/` — the gates; `docs/conformance.md` — the conformance allow-list record.

## Publish workflow
```
python3 tests/run_all.py                    # every gate (link check, contracts, stac-check, rashid)
python3 tools/render_thumbnails.py          # vector thumbnails (needs chiitiler)
python3 tools/publish.py                    # dry run
python3 tools/publish.py --confirm          # upload metadata (needs AWS creds)
python3 tools/upload_data.py [--confirm]    # staged data files
```
- Change detection: local size+MD5 vs the object's size+ETag; the remote side is listed
  **non-recursively** per catalog directory (a recursive listing of the prefix would walk
  every COG/parquet sharing it). A changed content-type mapping needs `--force`.
- `--confirm` refuses to guess. It aborts if ANY listing fails — the per-directory walk and
  the recursive path past 64 directories both raise — or if there is no lister at all: a
  failed listing is not proof that objects are absent. A dry run still works and shows every
  file as changed. `upload_data.py` refuses a blind `--confirm` the same way (an empty or
  failed listing).
- **Raster year snapshot:** `publish.py` overwrites a `raster/{year}/*` object only while the
  bucket still holds the ETag recorded in `tools/raster_snapshot.json`. One that changed
  out of band is **skipped** — the rest of the catalog still publishes — and the run exits
  non-zero; the dry run marks the same keys. A successful upload re-records them (commit the
  file), so a publish this repo made is not mistaken for a third-party one next time. Re-record
  the whole file with `AWS_PROFILE=source-coop AWS_ENDPOINT_URL=https://data.source.coop python3
  tools/raster_snapshot.py` (read-only). The guard covers the ~54 year-level files only, not the
  generated item/zone tree below them; `--force` lifts it along with the listing.
  `tests/test_publish.py` fails if a local `raster/{year}/*` key is missing from the snapshot,
  so it cannot rot silently.
- Publishing **never deletes**, and never delete bucket objects without asking Chris.
- CI (`.github/workflows/ci.yml`) sets `CI_LIGHT=1`: asset hrefs with data suffixes are
  exempt from the link check there (bytes live in the bucket, not git). Run gates locally
  without `CI_LIGHT` where the bytes are reachable.

## Conformance
Portolan **v0.2.0** (schema URI on every catalog/collection; absolute `self` on the
published root; `file:size` + multihash `1220…` `file:checksum` on every asset). Validated
by `rashid>=0.1.8,<0.2.0` — installed in `.venv/` (with stac-check + pyflakes). Never
widen `ACCEPTED` in `tests/test_portolan_conformance.py` without a row in
`docs/conformance.md`.

## Rails cluster (account `bgtj-tgirails`)
Claude runs on the **login node** (compute nodes lack internet) and drives Slurm via
`sbatch`/`squeue`/`sacct` + log tailing. Partitions `cpu` (512G/2TB) and `cpu_amd`
(~256G). `TMPDIR` on `/u`, never `/tmp` (tmpfs, counts against the job cgroup).
tylertoo built on-cluster at `~/tylertoo-src`; python venv `~/ftw-us-tiles/venv`
(duckdb, gpio, pmtiles, shapely, pyproj); aws CLI at `/u/cholmes/micromamba/envs/ftw/bin`.
DuckDB over the bucket: `https://data.source.coop/...` URLs (s3:// hangs), browser-like
User-Agent for the list API, `SET http_retries=20`.
