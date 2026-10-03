# ftw-global-data-catalog

The catalog metadata and the processing pipeline for the **Fields of the World
(FTW) Global Data 2nd Edition**: 1,139,401,371 predicted agricultural field
boundaries and 67,197 field/boundary-probability rasters, covering 2017
through 2025 worldwide.

This repository holds two things. `catalog/` **is** the published
[Portolan](https://www.portolan-sdi.org/)/STAC catalog, synced 1:1 to Source
Cooperative, so a merged change to it appears in the published catalog on the
next publish. `pipeline/` holds the code that produced the data, from
Sentinel-2 mosaic download through model inference to the released GeoParquet.

The data itself is never committed here. It lives in the
`ftw/global-data-2e` bucket, and the repository carries only the metadata that
describes it.

| | |
|---|---|
| Interactive map | <https://research.taylorgeospatial.org/global-ftw-2e/web/> |
| Portolan browser | <https://browser.portolan-sdi.org/#/external/data.source.coop/ftw/global-data-2e/catalog.json> |
| Files and downloads | <https://source.coop/ftw/global-data-2e> |
| Catalog root | <https://data.source.coop/ftw/global-data-2e/catalog.json> |
| Project | <https://fieldsofthe.world> |

## Installation

The gates and the catalog tools need Python 3.11 or newer and a virtualenv:

```bash
python3 -m venv .venv
.venv/bin/pip install 'rashid>=0.1.8,<0.2.0' stac-check pyflakes duckdb
```

The pipeline stages have their own dependencies, installed per stage. See
[pipeline/README.md](pipeline/README.md), which also explains which stages can
be reproduced from public inputs and which need credentials or an
unreleased package.

## Usage

Run every gate before committing. They check link resolution, the publish
contract, STAC validity through stac-check, and Portolan conformance through
rashid:

```bash
python3 tests/run_all.py
```

Publishing is two steps, and a dry run comes first:

```bash
python3 tools/publish.py            # dry run: what would change
python3 tools/publish.py --confirm  # upload metadata; needs AWS credentials
```

Data files are staged under `staging-data/`, which is gitignored, and uploaded
separately with `python3 tools/upload_data.py`. Publishing never deletes, so
removing a file from `catalog/` does not remove the object from the bucket.

Regenerate the catalog rather than hand-editing it. `tools/build_vector_items.py`
generates the vector tree and `tools/build_raster_items.py` the raster tree,
both from the published index manifests, so counts and sizes are measured
rather than retyped. `tools/render_thumbnails.py` renders each collection's
thumbnail from its own published style. Edit a generator and re-run it; never
edit its output.

## Layout

- `catalog/` — the published catalog, synced 1:1 to the bucket. Everything in
  it is published, and nothing outside it ever is.
- `pipeline/` — the five processing stages, documented in
  [pipeline/README.md](pipeline/README.md).
- `tools/` — the publisher, the data uploader, and the catalog generators.
- `tests/` — the CI gates.
- `docs/plan.md` — the build plan. `docs/conformance.md` — the conformance
  allow-list record.

## Contributing

Found a wrong license, a broken link, or a number that disagrees with the
data? Open an [issue](https://github.com/fieldsoftheworld/ftw-global-data-catalog/issues)
or a pull request. CI validates every change, so run `python3 tests/run_all.py`
before pushing and edit the generator rather than the generated output.

Do not widen the `ACCEPTED` allow-list in
`tests/test_portolan_conformance.py` to make a check pass. Fix the finding, or
record a waiver in `docs/conformance.md` with a tracking issue.

## License

The code in this repository is licensed under [Apache-2.0](LICENSE); see
[NOTICE](NOTICE) for third-party attributions. The published catalog metadata
under `catalog/` and the data it describes are licensed under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/), as declared on
every collection.
