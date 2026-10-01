# ftw-global-data-catalog

Git-backed [Portolan](https://www.portolan-sdi.org/)/STAC catalog for the
**Fields of the World (FTW) Global Data beta release** on
[Source Cooperative](https://source.coop/ftw/global-data-beta).

This repository is the source of truth for the catalog **metadata only**. The
data — 383,570,287 field polygons as per-UTM-zone GeoParquet for 2020, 2024
and 2025, and 67,197 field/boundary-probability COGs for 2017–2025 — lives in
the `ftw/global-data-beta` bucket and is never committed here.

- Published catalog root: <https://data.source.coop/ftw/global-data-beta/catalog.json>
- Data browser: <https://source.coop/ftw/global-data-beta>
- What the data is, and is not: [`catalog/README.md`](catalog/README.md)

## Layout

- `catalog/` — the published catalog, synced 1:1 to the bucket. Everything in
  it is published; nothing outside it ever is. Each level carries a `README.md`
  for people and an `AGENTS.md` for agents.
- `tools/` — publisher (`publish.py`), data uploader (`upload_data.py`), and
  the catalog builders. `build_vector_items.py` and `build_raster_items.py`
  **generate** their trees — collection JSON, item JSON, README.md and
  AGENTS.md alike. Edit the generator and re-run it; an edit to the generated
  output is overwritten by the next build.
- `pipeline/` — the rails Slurm pipeline that builds the per-year PMTiles
  products (see `pipeline/README.md`).
- `tests/` — the CI gates: link resolution, publish contract, STAC validity
  (stac-check), Portolan conformance (rashid).
- `docs/plan.md` — the build plan; `docs/conformance.md` — the conformance
  allow-list record.

## Workflow

```bash
python3 tests/run_all.py            # run every gate
python3 tools/publish.py            # dry run: what would change
python3 tools/publish.py --confirm  # upload metadata; needs AWS credentials
```

Publishing never deletes: removing a file from `catalog/` does not remove the
object from the bucket. Data files are staged under `staging-data/`
(gitignored) and uploaded with `tools/upload_data.py`.

Found a problem in the metadata? Open an
[issue](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues)
or a pull request — CI validates every change.

## Limitations of the data this describes

Worth knowing before writing documentation or examples against it, and stated
in full in [`catalog/README.md`](catalog/README.md):

- These are **model predictions**. In the FTW project's own words, a field
  here is a *remote-sensing field unit* (a connected component of predicted
  field-interior pixels), **not** a cadastral/legal parcel, and
  [this is not a land-tenure product](https://source.coop/ftw/global-data).
  One legal parcel may map to many polygons or to none.
- Produced by the FTW `unet_balanced_fp32.onnx` model on the Sentinel-2
  quarterly cloudless mosaics, vectorized by BoundaryVote instance
  post-processing. The checkpoint and its model card are released by the
  [FTW project](https://fieldsofthe.world) separately from this data.
- The vector `score` column is a `uint8` 0–100: the mean model field
  probability inside the parcel × 100. It is a ranking for filtering, not a
  calibrated confidence; no calibration against ground truth is published for
  this beta.
- Parcel counts and areas are predicted quantities. No land-cover masking was
  applied upstream, so water, scrub and built-up ground can appear as parcels.
- Every number in the generated documentation is measured from
  `index/*.parquet` or the committed item metadata, never from prose memory.
  Keep it that way: if a count is not measured, do not write it.

## License

The code in this repository is licensed under [Apache-2.0](LICENSE); see
[NOTICE](NOTICE) for third-party attributions. The published catalog
metadata under `catalog/` and the data it describes are licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/), as declared on
every collection.
