# FTW Global (beta) — Vector field boundaries

Per-year collections of predicted agricultural field boundaries (2020, 2024, 2025): **383,570,287 parcels** total, as per-UTM-zone cloud-native GeoParquet. Part of [Fields of the World](https://fieldsofthe.world) — agricultural field boundaries delineated from Sentinel-2 imagery.

Browse it in the [data browser](https://source.coop/ftw/global-data-beta); [AGENTS.md](./AGENTS.md) beside this file is the agent guide.

Data license: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)

## Collections

| Collection | Parcels | Files | Documentation |
|---|---|---|---|
| [2020](./2020/collection.json) | 129,270,485 | 54 UTM zones | [README](./2020/README.md) · [AGENTS](./2020/AGENTS.md) |
| [2024](./2024/collection.json) | 120,251,932 | 54 UTM zones | [README](./2024/README.md) · [AGENTS](./2024/AGENTS.md) |
| [2025](./2025/collection.json) | 134,047,870 | 54 UTM zones | [README](./2025/README.md) · [AGENTS](./2025/AGENTS.md) |

Every collection carries the same 9-column fiboa/vecorel schema, the same `zone=NN` hive layout and the same four map styles, so a query written against one year runs against any of them. New years drop in incrementally alongside these.

## Query a whole collection (2025)

```python
import duckdb
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
con.execute("""
    CREATE SECRET (TYPE s3, PROVIDER config, REGION 'us-west-2',
                   URL_STYLE 'path')
""")
glob = "s3://us-west-2.opendata.source.coop/ftw/global-data-beta/vector/2025/zone=*/utm*.parquet"
con.sql(f"""
    SELECT zone, count(*) AS parcels
    FROM read_parquet('{glob}', hive_partitioning=1)
    GROUP BY zone ORDER BY parcels DESC LIMIT 5
""").show()
```

The glob needs the `s3://` endpoint — DuckDB cannot expand a wildcard in an http URL — and Source Cooperative needs `URL_STYLE 'path'`. The bucket is anonymous-read, so the secret carries no credentials. `hive_partitioning=1` is what turns the `zone=NN` directory into a `zone` column; it arrives as a string, zero-padded, so compare it as `zone = '31'`. Swap the year in the glob to read a different collection.

## Coordinate system

Every zone file stores `geometry` as WGS 84 lon/lat (**EPSG:4326**), not in its UTM zone — the zone is a partition key, so a cross-zone or whole-year read needs no reprojection and the `bbox` struct can be compared across zones directly. Because the coordinates are degrees, `ST_Area` and `ST_Length` on `geometry` return degree-based numbers that mean nothing on the ground: read `metrics:area` (m²) and `metrics:perimeter` (m) instead, or reproject to an equal-area CRS first.

Each year's PMTiles archive is Web Mercator (EPSG:3857), the tiling CRS, and its cell aggregates were computed before that reprojection.

## Limitations

These are **model predictions**, not a survey. In the FTW project's own words, a field here is a *remote-sensing field unit* (a connected component of predicted field-interior pixels), **not** a cadastral/legal parcel, and [this is not a land-tenure product](https://source.coop/ftw/global-data); one legal parcel may map to many polygons or to none. Parcel counts, areas and perimeters are therefore predicted quantities that carry the model's errors, not measurements of anything surveyed.

- **Model provenance.** The FTW `unet_balanced_fp32.onnx` model, run on the Sentinel-2 quarterly cloudless mosaics and vectorized by BoundaryVote instance post-processing; each collection's README and `description` record the exact chain. The checkpoint and its model card are released by the [FTW project](https://fieldsofthe.world) separately from this data.
- **`score` is a model probability, not a validated confidence.** It is the mean field probability the model assigned to the pixels inside the parcel, × 100 and rounded into a `uint8` (0–100). Use it to rank and filter; no calibration against ground truth is published for this beta, so a score of 80 is not an 80% chance that the parcel is real.
- **Weaker outside the training distribution.** FTW describes the confidence on its earlier global release as "conservative outside the FTW training distribution (e.g. smallholder systems): real fields there may receive low confidence" ([FTW](https://source.coop/ftw/global-data)). Expect the same shape of error here, and prefer a continuous `score` over a hard threshold in smallholder regions.
- **No land-cover masking.** Nothing upstream removed non-agricultural ground, so water, scrub and built-up land can appear as parcels; `score` is the filter the dataset gives you. Parcels larger than 5 km² were dropped in post-processing.
- **Each year is an independent prediction.** `id` is unique within a year's collection and carries no meaning across years, so year-over-year comparison needs a spatial join, not an id join.

Found something wrong? Open an [issue](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues).

## Fixing this metadata

`catalog/` in [the repository](https://github.com/fieldsoftheworld/ftw-global-data-catalog) **is** this catalog: it syncs 1:1 to the bucket through `tools/publish.py`, so a merged change lands here on the next publish. Publishing never deletes, and no data bytes live in git — the repository carries only the metadata that describes them.

Every file in this directory is **generated** by `tools/build_vector_items.py`. Edit that generator and re-run it; an edit to the generated output is overwritten by the next build.
