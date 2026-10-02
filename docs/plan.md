# ftw-global-data-catalog — Portolan catalog for the FTW Global Data **beta** release

## Context

FTW's global field-boundary predictions have a new **beta** release on Source Cooperative at
`ftw/global-data-beta`. The bucket holds data only, no metadata: `vector/{2024,2025}/utm{NN}.parquet`
(54 UTM zones/year, 227.5 GiB, 120M+134M parcels, fiboa/vecorel schema), `raster/{2017..2025}/`
(67,197 two-band uint8 field/boundary-probability COGs, 2.5 m, ~26 TB), and `index/{raster,vector}.parquet`
manifests (hrefs, sizes, bboxes, per-tile stats — the basis for STAC generation).

Goal: a clean, git-backed Portolan catalog at `github.com/fieldsoftheworld/ftw-global-data-catalog`
(repo does not exist yet; local dir is empty). It borrows proven pieces from `~/repos/ftw-data-catalog`
(the alpha catalog) — especially the `scripts/tiles/` gpio+tylertoo PMTiles pipeline behind
`predictions/vectors-test/fields-yearly` (a5-r7 `cells` layer z0–8 → `fields` polygons z9–13, one
archive/year) — while hewing closer to portolan-catalog-template's refinements. First deliverables:
**2025 fields PMTiles carrying all parquet columns**, and a fleshed-out **COG predictions catalog with
per-item thumbnails**. Predictions derive from the [TGE Labs Sentinel-2 quarterly cloudless mosaics](https://source.coop/tge-labs/sentinel-2-quarterly-cloudless-mosaics/)
(CDSE mirror) — COG GDAL metadata already carries `source_collection`/`source_items` provenance.

**User decisions:** overlay metadata on the existing bucket layout; COG items generated to S3 (not
committed) per the alpha features pattern; per-item COG thumbnails too; repo follows the alpha
pattern but adopts the template's refinements; plan finalized locally, **all execution moves to a
Claude Code session on the rails cluster, remote-controlled from phone + desktop**. Vector data
exists for 2024 too and rasters for 2017–2025; 2026 will arrive later — everything is structured
per-year so years drop in incrementally.

## Phase 0 — Claude Code on rails + Remote Control

1. Login node: `curl -fsSL https://claude.ai/install.sh | bash` (no root). If `claude --version`
   hits a GLIBC symbol error (rails is glibc 2.28), fetch the `linux-x64-musl` static binary from
   `https://downloads.claude.ai/claude-code-releases/{VERSION}/manifest.json`.
2. Create + clone the repo (Phase 1 can start locally: `gh repo create fieldsoftheworld/ftw-global-data-catalog`),
   `cd` in, run `claude`, `/login` (full OAuth: open printed URL on phone/Mac, paste code back —
   NOT `setup-token`, which can't do Remote Control). Accept workspace trust.
3. `tmux new -s claude` → `claude --remote-control` (interactive mode: retries network outages
   indefinitely; server mode dies after ~10 min offline). Detach `Ctrl-b d`.
4. `/config`: enable both mobile push toggles. Connect from phone (Claude app → Code tab / QR) and
   desktop (claude.ai/code or Desktop app).
5. Claude stays on the **login node** (compute nodes lack internet); drives Slurm via
   `sbatch`/`squeue`/`sacct` + log tailing. Fallback: ssh/mosh + `tmux attach`.
   Re-run `/login` before multi-day campaigns (expiry stalls runs — happened during planning).

Rails facts (from alpha CLAUDE.md): account `bgtj-tgirails`; partitions `cpu` (512G/2TB),
`cpu_amd` (~256G); `TMPDIR` on `/u`, never `/tmp`; tylertoo built on-cluster (`~/tylertoo-src`,
`PROTOC=/u/cholmes/micromamba/envs/ftw/bin/protoc cargo build --release`); venv `~/ftw-us-tiles/venv`;
aws CLI `/u/cholmes/micromamba/envs/ftw/bin`; DuckDB needs `https://data.source.coop/...` (s3:// hangs)
+ browser-ish User-Agent + `SET http_retries=20`.

## Phase 1 — Repo bootstrap

Alpha's clean publish-directory model + the template's refinements:

```
catalog/                    # THE published catalog — synced 1:1 to ftw/global-data-beta
catalog.publish.yaml        # write_prefix: s3://us-west-2.opendata.source.coop/ftw/global-data-beta/
                            # public_base: https://data.source.coop/ftw/global-data-beta
                            # region: us-west-2, publish_dir: catalog, data_dir: staging-data
tools/                      # template naming (not scripts/catalog/)
  publish.py                # port alpha's (non-recursive listing, content-types, size+MD5 skip); fix its ROOT-path bug
  upload_data.py            # template pattern: data uploads gated by suffix + sentinel guard, never deletes
  build_raster_items.py     # NEW: per-year COG collections/items/items.parquet from index/raster.parquet (model: alpha build_features_items.py)
  build_vector_items.py     # NEW: per-zone items for vector/{year} from index/vector.parquet
  make_thumbnails.py        # port alpha's rasterio+matplotlib; extend for per-item COG thumbnails
pipeline/                   # the rails tiles pipeline (alpha scripts/tiles/, adapted): stage_global.py,
                            # aggregate_cells.sbatch, add_coverage.py, tile_cells.sbatch, tile_fields.sbatch, tile_weights.py
tests/                      # template style: run_all.py entry; port alpha's test_links, test_publish,
                            # test_stac_valid (stac-check), test_portolan_conformance (rashid ≥0.1.8, --no-data, allow-list)
.github/workflows/ci.yml    # CI_LIGHT=1 (template pattern: asset hrefs with data suffixes exempt in CI)
CLAUDE.md, README.md
```

Portolan v0.2.0 schema URI on catalogs+collections; `file:size` + multihash `1220…` `file:checksum`
on every asset; every catalog/collection dir carries README.md (`rel: describedby`), AGENTS.md
(`rel: agents`), thumbnail.png; root carries `vcs`/`issues` links (absolute GitHub URLs)
+ alpha's `git:*` fields; relative structural links, absolute `self` on published root.

## Phase 2 — Catalog skeleton (committed metadata)

```
catalog/
  catalog.json  README.md  AGENTS.md  thumbnail.png  .portolan/metadata.yaml
  vector/
    catalog.json                    # children: 2024, 2025, fields-yearly
    2024/collection.json + 54 items (utm01…utm60) + items.parquet ref   # committed (small)
    2025/  (same)
    fields-yearly/                  # the PMTiles handover product (Phase 3)
  raster/
    catalog.json                    # children: 2017…2025 year collections
    2017…2025/collection.json       # committed; items generated to S3 (Phase 4)
```

- Vector source collections: one item per UTM-zone parquet (108 total — committed), `table:columns`
  for all 20 columns with fiboa/vecorel schema links, extents from `index/vector.parquet`;
  `items.parquet` collection-mirror (role `collection-mirror`) generated with `portolan stac-geoparquet`.
- Provenance on every collection: providers = Microsoft AI for Good Research Lab (producer,
  processor) + Taylor Geospatial (producer, licensor, processor, host) as in alpha — **confirm at
  execution checkpoint**; license CC-BY-4.0 (**confirm**); `derived_from` link to the TGE Labs
  Sentinel-2 mosaics; processing notes from the parquet `collection` metadata key (BoundaryVote
  post-processing, >5 km² parcels removed) and COG GDAL metadata (`model=unet_balanced_fp32.onnx`,
  16 input bands).
- Descriptions follow the documentation contract: every URL a markdown link, "data browser"
  phrasing, no unedited AGENTS.md stubs; all documented queries actually run.

## Phase 3 — 2025 fields PMTiles (first deliverable; on rails)

Adapt `alpha:scripts/tiles/` (README there has measured timings). Beta is ~12× smaller than alpha
(134M vs 1.58B features) so expect hours, not days:

1. **Stage**: read `vector/2025/utm*.parquet` (https URLs), **carry all columns** (drop only
   `bbox` struct; keep `id`, `collection`?—drop constant columns like `collection` from tiles but
   keep in parquet). Check for cross-zone duplicates first (ids are tile-scoped; query
   `ftw:touches_window_edge` overlap) — dedupe only if measured. `SET TimeZone='UTC'` moot (no
   datetime col) — verify. `gpio` merge → GeoParquet 2.0 (row-group pruning).
2. **A5 aggregate**: `gpio process aggregate a5 --resolution 7`; metrics adapted to beta schema:
   `count`, `area_ha` (from `metrics:area`), `avg_field_prob`, `avg_boundary_prob`, `pct_covered`
   via ported `add_coverage.py` (geodesic constant r7 ≈ 2,075.5 km², antimeridian wrap, rounding).
   No 350 km² cutoff needed (beta already caps at 5 km²) — verify max area first.
3. **Cells archive**: `tylertoo tiles … --min-zoom 0 --max-zoom 8 --layer-name cells --verbatim
   --exclude-property a5_cell --profile bounded`; `tile_weights.py` report (≤600 KB gzipped from z2).
4. **Fields shards + handover merge**: shard-plan → coarse (plan-writer only) → shards z9–13
   (`--row-group-size 100000`) → `tylertoo merge` with the cells archive as COARSE. At 134M
   features, possibly fewer shards / no 360G node needed — size from the staged parquet.
5. **Styles**: per-year handover styles (cells `maxzoom: 9` / fields `minzoom: 9`, same PMTiles
   source), all `step`/`match` expressions (browser legend rule). Proposed set for beta:
   count, coverage, avg-size, **field-prob** (replacing alpha's confidence); *style-plan checkpoint
   with measured distributions before tiling*.
6. **Publish**: `fields-2025.pmtiles` + `cells_a5r7_2025.parquet` → `vector/fields-yearly/` via
   `aws s3 cp` from rails; collection.json (assets with file:size/checksum, `rel: pmtiles` +
   `pmtiles:layers: ["cells","fields"]`, `portolan:styles`, one default style), README, AGENTS.md,
   chiitiler thumbnail (portolan-thumbnails skill). 2024 runs later with the same scripts.

## Phase 4 — Raster (COG) catalog + thumbnails (on rails)

**Layout ruling (2026-10-01, user-approved):** raster uses full per-item folders,
vector-style — `raster/{year}/{tile}/` holds `{tile}.tif`, `{tile}.json` and
`{tile}.thumb.png` (PORTO-CORE-071). The existing 67,197 flat COGs are
server-side copied into the folders by `tools/move_raster_to_hierarchy.py`
(old flat keys deleted only after the index/metadata flip, with separate
approval), and the inference pipeline now emits this hierarchy directly, so
new generations never need a relayout. `index/raster.parquet` hrefs flip to
the folder keys. llms.txt is removed from the catalog (same ruling).

1. `tools/build_raster_items.py` reads `index/raster.parquet` (all fields needed: href, size,
   bbox, epsg, field/boundary/cropland fracs) + a one-time COG-header pass for proj:transform/shape
   (or derive from index bbox+known 40032² grid). Emits per year: collection.json (committed),
   ~7,466 items **straight to S3** at `raster/{year}/{tile}/{tile}.json` next to each `.tif` (relative
   asset hrefs), and `items.parquet` collection-mirror. (The browse-subcatalog idea here named
   alpha's `build_features_items.py` as the model; that script has no zone/gzd tree — it publishes
   S3-only items with no item links at all, which is where PTL-COL-005 comes from. Measured
   outcome and the two layouts that clear it: docs/conformance.md, "Open: the raster collections
   publish no item connectivity". **Decision pending** — a browse tree cannot carry items that
   live outside its own directories.)
   Items carry proj + file + render extensions; bands metadata (field, boundary, scale 1/255,
   quantization) from the verified gdalinfo; `derived_from` links to the four Sentinel-2 quarter
   source items recorded in GDAL metadata.
2. **Thumbnails**: extend `make_thumbnails.py` — per-item PNG from each COG's smallest overview
   (rasterio decimated read of the `field` band, colormap, nodata→alpha, composite over `#0b1414`),
   uploaded next to the item (`raster/{year}/{tile}/{tile}.thumb.png`, item asset role `thumbnail`). Run as an
   sbatch array on rails (67k renders, embarrassingly parallel, data in-region). Collection
   thumbnails: low-zoom mosaic per year. Start with 2025, then batch 2017–2024.
3. **Global overview COGs (per year)**: mosaic each year's 7,466 tiles at overview resolution
   (gdalbuildvrt over `/vsis3` reading the built-in overviews), warp to EPSG:3857 at a global-scale
   resolution (~80–160 m/px — sized so the file stays a few GB), apply a colormap to the `field`
   probability band → RGB(A) uint8 COG (`raster/{year}/overview.tif` or similar). Registered as a
   collection-level asset with roles `["visual","overview","cloud-optimized"]` (the alpha
   confidence-collection pattern), so each year renders at global scale in the browser. Runs on
   rails (in-region reads). 2025 first, then batch the other years.
4. CI: template `CI_LIGHT` exemption covers S3-only items (alpha has the documented pattern);
   conformance of the generated tree verified against the published catalog.

## Phase 5 — QC + publish

- `python3 tests/run_all.py`; `rashid check catalog --no-data`
- Browser QC per collection: default style renders at full extent, legends match measured values,
  tight bboxes, first tile load small (pmtiles.io), thumbnails show data not basemap
- `tools/publish.py` dry-run → `--confirm`; then `rashid`/`portolan check --live --url
  https://data.source.coop/ftw/global-data-beta`
- Rerun every AGENTS.md query against published data
- Register later via `register-catalog` skill (ask user first)

## Documentation decisions

Applied from the Portolan best-practices specs
([documentation](https://github.com/portolan-sdi/portolan-spec/blob/main/specs/best-practices/documentation.md),
[philosophy](https://github.com/portolan-sdi/portolan-spec/blob/main/specs/best-practices/philosophy.md)):

- **Two files, two audiences.** README.md for a person deciding whether to trust the data;
  AGENTS.md for an agent that has already committed and needs the first query to work. No copying
  between them. Every level cross-links its parent, its children and its sibling file.
- **llms.txt dropped** (2026-10-01, user's call). It was a third surface duplicating the other two
  and drifting from the hive layout; the `rel: llms` links went with it at the root and in the
  vector tree. rashid 0.1.8 stays green without them. The raster tree still carries llms.txt —
  removing those belongs to the agent that owns `tools/build_raster_items.py`. The llms.txt objects
  already in the bucket are untouched: publishing never deletes.
- **Lead with what a reader can do.** Each README opens with measured numbers, then a runnable
  single-file query, then the whole-collection hive glob — the pattern a reader would not have
  guessed. Every example is run before it is committed.
- **Say what the data is not.** A `Limitations` section at the root and in the vector tree quotes
  FTW's own framing (remote-sensing field unit, not a cadastral/legal parcel; not a land-tenure
  product), names the model, and states that `score` is an uncalibrated model probability.
- **CRS with consequences, not just an EPSG code.** The vector GeoParquet is EPSG:4326 in every
  zone file — the UTM zone is a partition key, not a CRS — so `ST_Area` returns square degrees and
  `metrics:area` is the column to read. COGs are per-tile UTM; PMTiles are Web Mercator.

## Execution checkpoints (will ask before acting)

repo creation in fieldsoftheworld org → license/providers confirmation → style plan with
distributions (before tiling) → publish (file counts, sizes, warnings) → any bucket deletions (never
without asking).

## Verification

End-to-end: open `https://source.coop/ftw/global-data-beta` in the data browser — root catalog with
vector + raster trees, fields-yearly renders the 2025 handover (cells→fields at z9) with legends,
COG items open with thumbnails; DuckDB queries from AGENTS.md run as written; CI green on GitHub.

## Key files to port (from ~/repos/ftw-data-catalog)

- `scripts/catalog/publish.py` (fix `parents[1]`→correct root), `scripts/catalog/make_thumbnails.py`
- `scripts/tiles/{stage_global.py,aggregate_cells.sbatch,add_coverage.py,tile_cells.sbatch,tile_fields.sbatch,tile_weights.py,README.md}`
- `scripts/features/build_features_items.py` (model for raster items/browse tree/places)
- `tests/{test_links.py,test_publish.py,test_stac_valid.py,test_portolan_conformance.py}`, `.github/workflows/ci.yml`
- `catalog/predictions/vectors-test/fields-yearly/{collection.json,styles/*.json,README.md,AGENTS.md}` (metadata + style templates)
