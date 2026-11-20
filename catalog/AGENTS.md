# AGENTS.md — Fields of the World — Global Data (2nd Edition)

The 2018, 2020, 2021, 2022, 2023, 2024 and 2025 collections use the updated processing documented in their year collections. Other year links retain historical published collections; they do not imply completion of the updated recipe for those years. Consult each collection for its provenance, footprint and asset inventory.

Guidance for AI agents and automated clients working with this Portolan/STAC
catalog. Every claim in this file is quoted from a source or measured from the
data.

Around this file: [catalog.json](https://data.source.coop/ftw/global-data-2e/catalog.json) is the normative root and
[README.md](https://source.coop/ftw/global-data-2e) is the human landing page. For a person rather than
an agent, the
[interactive map](https://research.taylorgeospatial.org/global-ftw-2e/web/)
renders the fields and the
[Portolan browser](https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/catalog.json)
walks this tree with each collection's own style. Each subtree has its own
guide — [vector/AGENTS.md](https://data.source.coop/ftw/global-data-2e/vector/AGENTS.md) for the field polygons and
[raster/AGENTS.md](https://data.source.coop/ftw/global-data-2e/raster/AGENTS.md) for the probability COGs — and so does
every per-year collection. Read the collection-level guide before querying;
it carries the measured numbers and the quirks this file does not repeat.

## Access

- Public URL base: `https://data.source.coop/ftw/global-data-2e/`
  (anonymous read), physically backed by
  `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/`. Data files
  (GeoParquet, COG, PMTiles) are hosted on Source Cooperative and referenced
  in place; the git repository carries metadata only.
- Resolve structural links relative to this object.
- Bucket layout:
  - `vector/{year}/zone=NN/utm{NN}.parquet` — field polygons, hive-partitioned
    by UTM zone; the updated 2018/2020/2021/2022/2023/2024/2025 collections contain 60 zones each. A whole-year read globs
    `s3://us-west-2.opendata.source.coop/ftw/global-data-2e/vector/{year}/zone=*/utm*.parquet`.
  - `vector/{year}/fields-{year}.pmtiles` and `vector/{year}/styles/*.json` —
    map tiles and MapLibre styles
  - `raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif` — two-band uint8
    field/boundary-probability COGs at 2.5 m, one folder per tile (the zone and
    GZD come from the tile key: `01KFS_0_0` → `zone=01/gzd=01K/`)
  - `index/vector.parquet`, `index/raster.parquet` — manifests listing hrefs,
    sizes and bboxes for every data file
- Use explicit HTTPS asset URLs for anonymous reads, with a browser-like User-Agent. Direct S3 clients use bucket us-west-2.opendata.source.coop, prefix ftw/global-data-2e/, region us-west-2 and path-style addressing through https://s3.us-west-2.amazonaws.com. Canonical HTTPS URLs remain unchanged. Listing and authenticated access depend on account permissions; anonymous bucket listing is not assumed. HTTPS URLs cannot expand wildcards, so enumerate assets from validated item or index inventories.

## Coordinate systems, with consequences

- The vector GeoParquet is WGS 84 lon/lat (EPSG:4326) in **every** zone file:
  the UTM zone is a partition key, not a CRS, so cross-zone and cross-year
  reads need no reprojection. The consequence is that `ST_Area`/`ST_Length` on
  `geometry` return degree-based numbers — read `metrics:area` (m²) and
  `metrics:perimeter` (m) instead, or reproject to an equal-area CRS first.
- The COGs are each in their own tile's UTM zone, so mosaicking across zones
  needs a warp; all years share one tile grid, so same-tile year-over-year
  comparison does not.
- The PMTiles archives are Web Mercator (EPSG:3857).

## What this data is not

- A parcel here is a *remote-sensing field unit*, **not** a cadastral/legal
  parcel. This is not a land-tenure product, so do not answer ownership,
  tenure or legal-boundary questions from it. See
  [Fields of the World](https://fieldsofthe.world).
- Check the actual year raster inventory before interpreting missing polygons. Coverage differs by year and version; absence of published polygons does not establish absence of fields.

- For 2018, 2020, 2021, 2022, 2023, 2024 and 2025, extraction uses a minimum mean field probability of 0.10, fills enclosed holes strictly smaller than 100 m², and uses a minimum area of 900 m². Independent crop filtering evaluates polygons with finite area from 900 through 5,000,000 m² against the union of native cropland observations from 2017, 2020 and 2024, removing polygons with less than 5% overlap. Unknown or incomplete coverage, nonfinite areas and polygons outside that size interval are retained by this filtering stage. Downstream vector cleanup is documented separately in each vector collection; its 20 m² hole-cleanup description is distinct from extraction hole filling.

- The vector `score` (uint8 0–100) is the mean model field probability inside
  the parcel × 100 — a ranking for filtering, not a calibrated probability.
  No calibration against ground truth is published for this 2nd Edition.
- Counts and areas are predictions, not measurements; report them as such.
  [README.md](https://source.coop/ftw/global-data-2e) has the full set of caveats with
  sources.

## Fixing this metadata

Validate changes against the exact year collection, source inventory, artifact checksums and schema/link checks before publication. The [catalog repository](https://github.com/fieldsoftheworld/ftw-global-data-catalog) retains the original generators; [production processing](https://github.com/taylor-geospatial/global-ftw-2e) records the updated recipe and versioned metadata adapters.
