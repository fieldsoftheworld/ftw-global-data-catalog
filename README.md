# ftw-global-data-catalog

Git-backed [Portolan](https://www.portolan-sdi.org/)/STAC catalog for the
**Fields of the World (FTW) Global Data 2nd Edition** on
[Source Cooperative](https://source.coop/ftw/global-data-2e).

This repository is the source of truth for the catalog **metadata only**. The
data — per-UTM-zone GeoParquet field polygons for 2024–2025 and ~67k
field/boundary-probability COGs for 2017–2025 — lives in the
`ftw/global-data-2e` bucket and is never committed here.

- Published catalog root: <https://data.source.coop/ftw/global-data-2e/catalog.json>
- Data browser: <https://source.coop/ftw/global-data-2e>

## Layout

- `catalog/` — the published catalog, synced 1:1 to the bucket. Everything in
  it is published; nothing outside it ever is.
- `tools/` — publisher (`publish.py`), data uploader (`upload_data.py`), and
  catalog builders.
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
