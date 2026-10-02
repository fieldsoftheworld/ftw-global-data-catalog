# AGENTS.md — Fields of the World — Global Data (beta)

Guidance for AI agents and automated clients working with this Portolan/STAC
catalog. Every claim in this file is quoted from a source or measured from the
data.

- Public URL base: `https://data.source.coop/ftw/global-data-beta/`
  (anonymous read). Data files (GeoParquet, COG, PMTiles) are hosted on
  Source Cooperative and referenced in place; the git repository carries
  metadata only.
- Resolve structural links relative to this object.
- The bucket layout is `vector/{2017..2025}/zone={NN}/utm{NN}.parquet` (per-UTM-zone
  GeoParquet field polygons) and `raster/{2017..2025}/` (two-band uint8
  field/boundary-probability COGs at 2.5 m), with
  `index/{vector,raster}.parquet` manifests listing hrefs, sizes, and bboxes.
- Vector (2017–2025) and raster collections are published here; the
  `index/*.parquet` manifests list every file with its href, size and bbox.
- When querying the parquet over HTTP with DuckDB, use
  `https://data.source.coop/...` URLs, not `s3://`, and set a browser-like
  `User-Agent` for the list API.
