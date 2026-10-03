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
  thumbnails, styles). Dotfiles are not published, except `.portolan/metadata.yaml`.
- `tools/` — `publish.py` (metadata, 1:1), `upload_data.py` (staged data, suffix
  allow-list, never deletes), `make_thumbnails.py` (COG thumbnail core).
- `pipeline/` — the rails Slurm PMTiles pipeline (alpha port; Phase 3 adapts it — see
  `pipeline/README.md` for cluster gotchas and measured timings).
- `staging-data/` — gitignored staging tree read by `upload_data.py`; keys mirror its
  layout under the write prefix.
- `tests/` — the gates; `docs/conformance.md` — the conformance allow-list record.

## Publish workflow
```
python3 tests/run_all.py                    # every gate (link check, contracts, stac-check, rashid)
python3 tools/publish.py                    # dry run
python3 tools/publish.py --confirm          # upload metadata (needs AWS creds)
python3 tools/upload_data.py [--confirm]    # staged data files
```
- Change detection: local size+MD5 vs the object's size+ETag; the remote side is listed
  **non-recursively** per catalog directory (a recursive listing of the prefix would walk
  every COG/parquet sharing it). A changed content-type mapping needs `--force`.
- Publishing **never deletes**, and never delete bucket objects without asking Chris.
- **Stale raster subtree:** `catalog/raster/{year}/{README.md,AGENTS.md,collection.json}` are
  older than the published ones (flat `{tile_key}.tif` paths, no `zone=NN` child links). The
  bucket's hive raster tree (per-zone/per-gzd catalogs, newer collections) was built by tooling
  that is not in this repo. A full `publish.py --confirm` overwrites those 27 objects with the
  stale copies; if that happens, restore them from the published versions (fetch the bucket's
  current copies before publishing). Fix: bring the raster tree generator in here.
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
