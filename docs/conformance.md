# Portolan Conformance

Conformance means passing [rashid](https://github.com/portolan-sdi/rashid),
not claiming to conform, so it runs in CI:

```bash
python3 tests/test_portolan_conformance.py
```

That gate fails on any error-severity finding whose rule is not listed below.
The list starts empty and it must never grow without a row here. A known
deviation with an issue number is a debt someone can pay off. A silently
widened allow-list is a false claim about what this catalog conforms to.

## The rashid version floor

The gate needs rashid `>=0.4.0,<0.5.0`. It reads `rashid --version` and fails
outside that range. It also fails when rashid is absent, and prints the install
command. A skip would report a green run for a catalog that no validator read.

The floor is 0.4.0 because it is the first rashid that bundles the Portolan
**v0.3.0** profile this catalog declares. An older one applies the v0.2 rule
set, so it never runs the rules the catalog was migrated to meet — the
one-file data asset (PORTO-FMT-065) and the listable `partition:glob`
(PORTO-FMT-064) — and reports a v0.3.0 schema URI as a version it does not
know. The same range is in the CI install step.

The upper bound stops an unreviewed 0.5 rule set from changing what this gate
means. Raise both bounds together when you move to 0.5, and read the new rules
first.

This file also records workarounds for the other validator CI runs. Those are
not conformance debts, because the catalog is correct and the validator is not.
They live here so nobody has to read CI code to find out why a gate skips
something.

## Accepted deviations

| Rule | Where | Why accepted | In `ACCEPTED`? | Tracking |
|---|---|---|---|---|
| PTL-DAT-009 | the 67,197 published 2e score COGs, `raster/{2017..2025}/.../{tile}.tif` | They were written without embedded band statistics. 26 TB cannot be rewritten in place without destroying the COG layout, and the writers are fixed, so the next generation complies. See the section below. | **no** — a data rule, never reached by the gate (see below) | — |

`ACCEPTED` in `tests/test_portolan_conformance.py` is **empty**, and the one
structural row that used to be in it (PTL-VIZ-002) is closed rather than
waived — see the section below. No structural rule is waived in this catalog.

<!--
When you accept one, add a row and a section explaining it.

A structural rule (`PTL-CAT/COL/LNK/AST/VIZ/FIL-…`) also needs its rule id in
ACCEPTED in tests/test_portolan_conformance.py. Both, or neither.

A **data** rule (`PTL-DAT-…`) does not, and must not be added: the gate runs
`rashid check --no-data`, so the byte checks never run there and the id would
waive nothing while reading as if the gate had seen the deviation and let it
pass. The row is the whole record. Say so in the row, as PTL-DAT-009 does.
-->

### PTL-VIZ-002: the raster browse overview has no style document (closed)

Each raster year collection carries an `overview.tif` with roles `visual`,
`overview`, `cloud-optimized`. PTL-VIZ-002 wants a visualization asset to name
the style that renders it, and this one has none to name: it is a pre-rendered
RGB JPEG COG, so the colormap is applied at build time and baked into the
pixels rather than evaluated by a client. A `style` asset pointing at a
MapLibre document would be a false claim about how the image is drawn.

**It no longer fires, and the waiver is gone.** The asset used to declare
`image/tiff; application=geotiff`, the plain GeoTIFF media type, while the
profile's media-type table requires the COG type for a COG — which this file
is, as its `cloud-optimized` role claims and as its internal overviews
confirm when read back over `/vsicurl`. The v0.3 migration corrected the type
to `image/tiff; application=geotiff; profile=cloud-optimized`, and rashid
0.4.0 then stops reading the asset as a derivative that needs a MapLibre
style: a COG's styling is the classified-vs-continuous raster question, not
the PMTiles one. Measured both ways — flipping that one media type back on
`raster/2017/collection.json` brings the finding straight back, 1×. The
seven re-processed years arrived from the publisher already declaring the
COG media type, so only 2017 and 2019 needed the correction. `ACCEPTED` is
empty, and this section stays as the record of why the rule
used to fire.

The colormap is documented instead — in each year's README.md and in
`pipeline/make_overview.py`, which is the code that applies it. The rule as
written does not distinguish a client-styled asset from a baked one; filed as
[rashid#202](https://github.com/portolan-sdi/rashid/issues/202). Nine
findings when every year declared the plain GeoTIFF media type.

Portolan v0.3.0 does not close this. Its Visualization Styles section still
says "Styling for continuous and multiband rasters is still under discussion"
and points at `specs/incubating/raster-styling.md`, and the one raster style
form the version does define is the classified case — a COG colour table with
`classification:classes`, which does not apply here: these are continuous
probability surfaces, and v0.3 says a continuous raster SHOULD NOT carry
`classification:classes` (PORTO-FMT-059). So the waiver stands until the
incubating raster-styling document lands, and `classification:classes` is
deliberately absent from both the probability COGs and the overview.

### PTL-DAT-009: the published 2e score COGs carry no embedded band statistics

**Rule.** Every COG band MUST carry `STATISTICS_MINIMUM`, `STATISTICS_MAXIMUM`,
`STATISTICS_MEAN` and `STATISTICS_STDDEV` embedded in the file's own
`GDAL_METADATA` tag (formats.md:95). rashid reads them with
`GDAL_PAM_ENABLED=NO`, so statistics in a `.aux.xml` sidecar do not count — and
a sidecar would not be published beside the bytes anyway. PTL-DAT-010 adds
`STATISTICS_VALID_PERCENT`: a SHOULD, and a MUST for a band that declares a
nodata value.

**Where it fires.** The 67,197 already-published 2e score COGs
(`raster/{year}/zone={ZZ}/gzd={GZD}/{tile}/{tile}.tif`, ~26 TB). The writer that
made them did not compute statistics, so rashid's data pass reports one
error-severity PTL-DAT-009 per asset — plus a PTL-DAT-010 warning, the SHOULD
form, because these bands declare no nodata.

This catalog publishes exactly two families of COG, and only one of them is
affected. Measured over the published bytes with
`gdalinfo --config GDAL_PAM_ENABLED NO` on 2026-10-04:

| Published COG | Statistics in the file |
|---|---|
| `raster/{2017,2020,2025}/overview.tif` (3 of the 9 sampled) | all five on all three bands; `nodata=0`, so PTL-DAT-010's MUST form is satisfied as well |
| `raster/{2017,2025}/…/01KFS_0_0.tif` (score COGs) | none on either band; `nodata=None` |

The per-item `{tile}.thumb.png`, the per-year `thumbnail.webp` and every
`.parquet` are not COGs, so the rule does not reach them.

**Ruling (Chris, 2026-10-04): accepted for 2e; fixed for the next generation.**
Statistics are a header fact, but there is no way to add one to a finished COG
in place. The tag lives in the TIFF directory ahead of the image data, so
growing it rewrites every subsequent offset: GDAL re-creates the file. At 26 TB
that is a full re-upload of the whole raster product to change four numbers per
band, and the alternative — letting GDAL write the statistics to PAM — produces
67,197 `.aux.xml` sidecars that satisfy nothing, because the rule is about what
is in the file.

So 2e keeps its COGs, and the writers were fixed instead:

- `pipeline/inference/run.py` `write_score` writes all five statistics per band
  from the array it already holds in memory, so they are **exact**, not
  estimated (no `STATISTICS_APPROXIMATE`), and they cost nothing — the array is
  there, and no second pass over the pixels happens.
- `pipeline/make_overview.py` computes them with `gdalinfo -approx_stats` over
  the source VRT, which the COG translate then carries into the output file, and
  `verify_band_stats` re-reads the finished COG with PAM off and refuses to
  publish one where they did not land. `-approx_stats` reads the warped zones'
  overviews rather than every base pixel, which the spec permits.

Both are locked in by tests rather than by review:
`pipeline/inference/test_inference.py::test_score_cog_embeds_exact_band_statistics`
writes a COG through the real writer, reads it with `GDAL_PAM_ENABLED=NO` and
compares all five values against numpy, and
`test_rashid_finds_no_statistics_defect_in_a_score_cog` runs rashid's own
`_check_cog_stats` over that file where rashid is installed.
`pipeline/test_make_overview.py` covers the overview gate, including a
stats-less COG that it must reject.

**Why no `ACCEPTED` entry.** Both rashid runs in this repo — the CI gate
(`tests/test_portolan_conformance.py`) and the pre-publish job
(`pipeline/rashid_check.sbatch`) — pass `--no-data`, so no `PTL-DAT-*` rule ever
fires in either. Adding the id to `ACCEPTED` would waive nothing and would claim
the gate had seen this deviation and accepted it. It has never read a byte of
these COGs. This row is the record; the gate is silent on it by construction.

**How it closes.** Not by editing the published files, and not by a validator
change: by the next generation of score COGs, which is written with the
statistics in it. Until then anyone running `rashid check catalog/` with the
data pass on the 2e tree should expect 67,197 PTL-DAT-009 errors and no others
of that family.

## Policy: no `file:checksum` on the raster COG assets

The raster items carry `file:size` (from `index/raster.parquet`) and no
`file:checksum`. PORTO-CORE-029 wants one; rashid reports its absence as a
**warning** (PTL-AST-003), not an error, so no `ACCEPTED` entry is needed and
the gate stays honest.

It is a deliberate policy, not an oversight. A multihash checksum means
reading the bytes, and the raster tree is 67,197 COGs totalling ~26 TB. The
index carries no checksum column (measured: 14 columns, none of them a
digest), so filling the field would mean streaming 26 TB — dozens of times the
whole vector tree — to add a field that no reader of this catalog has asked
for. The vector tree, at ~0.7 TiB in 486 zone parquets, was cheap enough to hash
(`tools/hash_remote.py`) and does carry checksums. The rule this follows is
the s2-stac-geoparquet one: checksums where they are cheap.

`tools/build_raster_items.py items` reads a `--sidecar` for header facts and
fills `file:checksum` the moment a digest is available for a tile, so the
policy reverses by producing the digests, not by editing the generator.

The same reasoning covers two collection-level assets: the per-year
`overview.tif` (a multi-GB mosaic) carries `file:size` from its HEAD response
and no checksum. The per-year `thumbnail.webp` is small, so the generator
downloads it and carries both.

## How the raster items are reachable (PTL-COL-005, closed)

67,197 raster items, ~7,466 per year. Until 2026-10-02 they were generated
straight to S3 and not committed, and the collections carried no `rel: item`
link, so enumeration went through `index/raster.parquet` and each
collection's `items.parquet` mirror alone. rashid 0.1.8 reported that as an
**error**, nine times:

```
PTL-COL-005  raster/{2017..2025}/collection.json: collection registers item
mirror 'mirror' but publishes no items; the mirror is a derived copy and the
item JSON remains the normative representation
(PORTO-CORE-015, PORTO-CORE-032, PORTO-FMT-042)
```

The finding was correct, not a validator artifact: PORTO-CORE-032 wants "a
`child` or `item` link for every object it contains", and 67,197 published
items had none. It is closed by publishing the connectivity, not by waiving
the rule — the items are grouped under browse subcatalogs (user ruling
2026-10-02; docs/plan.md Phase 4 has the layout). Verified: `rashid check
catalog --no-data` over the full nine-year tree reports **no PTL-COL-005 at
all**. That run was made under rashid 0.1.8, when PTL-VIZ-002 was the one
error-severity rule left and the only entry in `ACCEPTED`; both are gone
under v0.3.0 (see the section above), so the gate now passes with `ACCEPTED`
empty. The
measurements that chose that shape are kept below, because they also bound
what may and may not be changed about it later.

### The generated item tree is not committed (user ruling 2026-10-03)

The tree is **generated, not authored**: `tools/build_raster_items.py items`
rebuilds all 78,267 files (67,197 items, 3,690 zone/GZD catalogs, and
their 7,380 README/AGENTS files) from
`index/raster.parquet` plus the header sidecar, deterministically, and
`tools/publish.py` walks the filesystem rather than the git index, so what
publishes is unchanged. The 2026-10-02 ruling above was about the *bucket
layout*; committing the files was never the point, and they are gitignored
(`catalog/raster/*/zone=*/`).

What that costs, and how each gate carries it:

- **A fresh checkout cannot pass `rashid check` as-is**: the year
  collections' 54 `child` links per year point at zone catalogs that exist
  in the published bucket but not on disk, and rashid derives containment
  from directory nesting over the files it can walk (measured above), so a
  clean clone reports PTL-LNK findings for every such link and PTL-COL-005
  returns. Validating that half-tree would prove nothing about the
  published catalog, so `tests/test_portolan_conformance.py` **refuses to
  run on a checkout without the generated tree**: under `CI_LIGHT=1` it is
  a documented skip, and locally it fails with the regenerate command.
  `ACCEPTED` stays empty — no finding is waived, because no run that could
  produce those findings is accepted as valid.
- **The real conformance gate is pre-publish**: `pipeline/rashid_check.sbatch`
  runs the full nine-year tree (2 h 20 m measured, login nodes CPU-kill it)
  after regeneration, before `tools/publish.py --confirm`.
- **`tests/test_links.py`** treats a link into the absent generated tree
  like a data href: HEAD-checked against the published base in a full run,
  exempt under `CI_LIGHT`. With the tree on disk the links are checked on
  disk as before.
- **Upstream**: a catalog whose item tree is generated and bucket-resident
  cannot satisfy PTL-COL-005 from a clean checkout at all; filed as a
  validator feature request (validate against the published tree, or
  declare items bucket-resident): [rashid#205](https://github.com/portolan-sdi/rashid/issues/205).

### Containment is directory nesting, so the group must be in the key

rashid derives containment from the file tree: `CatalogGraph.parent_of` walks
up from a file's own directory to the nearest `catalog.json` /
`collection.json`, and the graph holds only JSON the walk finds on disk.
Measured on scratch copies of this catalog, before the layout changed:

| Tried | Result |
|---|---|
| Browse catalogs committed under `raster/{year}/browse/{zone}/`, `rel: item` links (relative **or** absolute under the published base) to bucket-side items | PTL-COL-005 **stays** (9×) *and* one new PTL-LNK-006 error per item link — "href … does not resolve to any file" — i.e. 67,197 new errors |
| The same, with the item JSONs on disk at `raster/{year}/{tile}/{tile}.json` | PTL-COL-005 clears, but the browse catalogs' item links become PTL-LNK-006 "points to the wrong object: must point to an item contained by this object", and PTL-LNK-002 demands a `rel: item` link on the **collection** for all 7,466 items instead |

Two rules follow, and both are load-bearing:

- **The item JSON must be in the checkout.** A collection whose items live
  only in the bucket cannot pass, whatever links it carries.
- **A subcatalog can only own items inside its own directory.** Grouping is
  therefore a property of the object key, which is why the data moved with
  the metadata rather than the metadata reaching back with `../../`.

`PTL-CAT-001` is the companion warning: its threshold is 20 *ungrouped*
children (`PORTO-CORE-078`, a SHOULD). Children that are themselves catalogs
do not count, so a year collection with 54 zone catalogs and no direct items
reports nothing at all, while a flat list of 7,466 could never comply.

### What the chosen layout costs

- **Git.** 67,197 items, 320 MiB in the working tree, **22 MiB for a full
  clone** (measured with `git bundle create … HEAD`: the items are
  near-identical, so they delta extremely well). Committing them leaves
  ~150k loose objects until someone runs `git gc`.
- **Files.** 3,690 group catalogs (486 zone + 3,204 GZD across nine years),
  each with a README.md and an AGENTS.md, because PTL-FIL-001/002/003 bind
  plain catalogs and not only collections (measured). That is 7,380
  generated documentation files.
- **Warnings, not errors.** Measured over the full tree (rashid job
  250634, 2026-10-03): 1,503
  PTL-CAT-001 (1,494 GZD leaves holding 20 or more tiles, plus the 9 vector
  collections that already warned) and 201,618 PTL-AST-003 (134,412 "no
  `file:checksum`" and 67,206 "no `file:size`"). The year collections
  themselves no longer warn, because all of their children are catalogs.
  A fourth level (the 100-km square) would clear the leaf warnings and is
  deliberately not used: every extra group catalog costs rashid a full-tree
  scan (below), so it would buy a warning-free report with an unusable gate.
  The `file:checksum` absences are the documented policy above; the 67,197
  thumbnail `file:size` absences are fillable from the recursive bucket
  listing the item uploader already does.

### Gate runtime, and why the sampling below exists

rashid's containment helpers cost (nodes × catalog-or-collection nodes):
`children_of` calls `parent_of` once per node, and `PTL-LNK-002` calls
`children_of` once per catalog. Measured, all on rails with rashid 0.1.8:

| tree | json files | catalogs + collections | wall |
|---|---|---|---|
| flat, 1 year | 7,658 | 13 | 32 s |
| flat, 3 years | 22,590 | 13 | 95 s |
| grouped, 1 year | 8,068 | 423 | 124 s |
| **grouped, 9 years (this catalog)** | **71,448** | **3,711** | **2 h 54 min** |

The flat rows are linear in file count, because their structural-node count
never moves. The grouped rows are not: one grouped year is 4× slower than a
flat one for 5% more files, because each of its 410 group catalogs triggers
a full-tree scan. 8,629 s of that 2 h 30 min is user CPU, single-threaded,
and it peaks at 1.4 GiB — it is compute, not memory.

Two consequences, both practical:

- **The gate does not run on a login node.** A nine-year run there was killed
  at 39 minutes, well under the 64 GiB cgroup limit, so it is a CPU-time
  policy rather than memory. `pipeline/rashid_check.sbatch` runs it as a
  Slurm job instead and reports `sacct` MaxRSS and elapsed. It needs no
  network: `--no-data` skips the byte checks and the schemas are bundled.
- **It cannot gate every commit.** An upstream perf fix is the real repair
  (precompute the containment map once per graph instead of rescanning per
  catalog). Until then the CI split has to be chosen deliberately and
  written down here — the gate must not quietly stop reading the tree.

`tools/publish.py` lists each catalog directory non-recursively, which over
67,197 item directories is 67,197 list calls. `tools/build_raster_items.py
items --confirm` exists for exactly that reason: it lists each year
recursively instead, nine calls in total, and publishes only item JSON.

## Policy: the link check samples the per-item bucket hrefs

`tests/test_links.py` resolves every relative href and, without `CI_LIGHT`,
HEAD-checks the ones whose bytes live only in object storage. The committed
item tree makes that 134,394 hrefs — a `data` and a `thumbnail` per item —
which is half an hour of requests aimed at one host on every gate run.

So those hrefs are **sampled per family**, a family being one collection
directory and one asset key (`raster/2017` × `data`), capped at
`LINK_SAMPLE` (default 40) and spread across the family by stride, so the
sample spans every UTM zone instead of clustering in zone 01. The gate
prints how many it checked and how many it sampled out, and `LINK_SAMPLE=all`
checks every one — run that before publishing.

Two things keep this honest. Structural links are never sampled: all 67,197
`rel: item` links resolve on disk, so checking every one costs no network.
And the exemption that lets an item's `{tile}.thumb.png` be checked over
HTTP at all reads the suffix and nothing else — `.thumb.png` is in
`DATA_SUFFIXES`, plain `.png` deliberately is not, because a collection's own
`thumbnail.png` is committed and must resolve on disk.

## Validator workarounds

### stac-check reports a dialect crash on every collection

`tests/test_stac_valid.py` exempts one stac-check failure:

```
'list' object has no attribute 'get'
[Schema: https://schemas.portolan-sdi.org/portolan/vX.Y.Z/schema.json]. Error in Extensions.
```

The Portolan schema is valid draft-07, and rashid validates catalogs against it
cleanly. `stac-validator`, which stac-check uses, hardcodes the JSON Schema
2020-12 dialect and ignores the `$schema` a schema declares. The profile schema
uses the draft-07 tuple form of `items` in `valid_bbox`, which means something
different under 2020-12, so the library raises instead of validating.

Tracked upstream at <https://github.com/stac-utils/stac-check/issues/159>,
and on the Portolan side at
<https://github.com/portolan-sdi/portolan-spec/issues/157>.

The exemption matches that exact message, and only when the failing schema is a
Portolan profile schema. Every other stac-check error still fails the build,
and the gate prints how many objects took the exemption.

The exemption expires on its own. The gate fails once stac-check stops emitting
the crash on a collection or item that declares the profile schema, and tells
you to delete both the exemption and this section. CI installs stac-check
unpinned, so the next release triggers that without anyone watching for it.

`tests/test_stac_valid.py` also fails when stac-check is absent, and prints the
install command. It takes no version floor and no pin. The rashid floor exists
because that gate asserts four named rules. This gate asserts no stac-check
rule. It needs the opposite property. A pin holds the exemption open after the
upstream fix ships.

### portolan check --live HEADed partition-glob asset hrefs literally (closed)

The year collections used to carry a `data` asset whose href was the partition
glob, beside `partition:glob`. `portolan check --live` HEAD-requested that
href literally, and a `*` URL returns no usable Content-Length, so PTL-LIV-002
reported one error per collection
([portolan-cli#914](https://github.com/portolan-sdi/portolan-cli/issues/914)).

Portolan v0.3.0 removed the construct: a data asset links to exactly one file
and its href MUST NOT contain `*` or `[` (PORTO-FMT-065), so the glob now
lives only in `partition:glob`, where it belongs. The glob asset is gone from
every year collection, and with it this finding. Nothing in the catalog
advertises a wildcard href any more, and every partition member is still
HEAD-verified individually through its item's own data asset
(`tests/test_links.py`).

## The Portolan v0.3.0 migration (2026-11-20)

What changed in the catalog, with the requirement each change answers:

| Change | Requirement |
|---|---|
| Schema URI `v0.2.0` → `v0.3.0` on the root, both tree catalogs and all 18 collections | PORTO-CORE-006, PORTO-CORE-009 |
| The vector year collections' glob `data` asset removed; the glob lives in `partition:glob` alone, and the collection description names it | PORTO-FMT-065 |
| `partition:glob` rewritten from the relative `./zone=*/utm*.parquet` to `s3://ftw/global-data-2e/...`, listable through the Source Cooperative data proxy | PORTO-FMT-064, PORTO-FMT-075 |
| `storage:schemes` added to the vector collections (storage extension v2.0.0, `type: custom-s3`, endpoint `https://data.source.coop`, path-style). rashid resolves the glob through it: without the entry the listing is denied, measured | PORTO-FMT-066 |
| `title` and `description` filled on every asset that lacked them: 528 item data assets, and per year `pmtiles`, `thumbnail`, `coverage`, `mirror`, `cells-a5r7`, `cells-pmtiles`, eight `webcells-a5r*` and four `styles/*` | PORTO-CORE-088 |
| The raster `overview` asset re-typed from `image/tiff; application=geotiff` to the COG media type, and given `start_datetime`/`end_datetime` | the media-type table in core.md; PORTO-FMT-072 |
| `item_assets` definitions and the generated raster item assets given descriptions | PORTO-CORE-088 |

Not changed, with the reason:

- **No `classification:classes`.** The probability COGs and the overview are
  continuous surfaces, and PORTO-FMT-059 says a continuous raster SHOULD NOT
  carry classes. See the PTL-VIZ-002 section.
- **No `nodata` on the probability COGs.** PORTO-FMT-073 asks satellite and
  aerial *imagery* to declare nodata. These are model output where 0 is a real
  value — 0% field probability — so declaring nodata 0 would tell clients to
  drop real pixels. `pipeline/make_overview.py` records the same reasoning for
  the mosaic, which does declare nodata 0 because its black border is genuinely
  absent data.
- **The whole-year https glob is not advertised as an asset.** It never worked
  as one (HTTP cannot list, so the wildcard never expanded), and v0.3 forbids
  it. The per-partition https URL and the s3 glob are both in the collection
  description, the README and AGENTS.md.
- **`file:checksum` on `overview.tif` for 2017 and 2019** stays absent, under
  the existing "no `file:checksum` on the raster COG assets" policy above: a
  multi-GB mosaic each, ~4.3 GB to read for the two. The seven re-processed
  years' overviews arrived from the publisher with checksums, so PTL-AST-003
  is down from 27 findings to 2, and it is a warning, so the gate stays honest
  without an `ACCEPTED` entry.
- **PTL-CAT-001** (warning, 9×) still fires on the vector year collections:
  each holds 54 or 60 `item` links with no subcatalog grouping them. Grouping
  them would change 528 published item URLs for a browse nicety; the raster
  tree is grouped because it has thousands of items a year, not dozens.

### The 1st-edition `child` link 404s until ftw-data-catalog republishes

The root's `child` link to the 1st edition points at
`https://data.source.coop/ftw/global-data/first-edition/catalog.json`.
`fieldsoftheworld/ftw-data-catalog` is becoming the FTW root catalog at
`ftw/global-data` and moving the 1st edition down into `first-edition/`
([ftw-data-catalog#11](https://github.com/fieldsoftheworld/ftw-data-catalog/pull/11)),
so that href is the post-restructure location and returns 404 until that PR
merges and republishes. `ftw/global-data/catalog.json`, the pre-restructure
location, still answers 200 today.

rashid reports nothing either way: the href is outside this catalog's root
`self` base, so the validator has no file tree to resolve it against
(core.md, "A root catalog that links a child in a different bucket therefore
gets no check on that link"). The live mode of `tests/test_links.py` does
check it, and reports one error until the restructure lands. CI does not see
it — `CI_LIGHT=1` skips every href whose bytes are not in the checkout — so
this is a finding for whoever runs the gate locally before publishing. Do not
publish this catalog's root until the link resolves.

### catalog/ now mirrors the published catalog (2026-11-20)

`catalog/` **is** the published catalog, and for most of 2026 this
repository's generators were what wrote it. That stopped being true before
this migration: the current generation was produced by the pipeline in
`taylor-geospatial/global-ftw-2e`, which is private and whose generator
changes are not here. Git described the previous generation, the bucket
served the current one, and the two had drifted:

| | git `main` before this branch | published, adopted here |
|---|---|---|
| vector partitions a year | 54 | **60** for 2018 and 2020–2025 |
| raster tiles a year | ~7,466 | **~14,580** for the same years |
| vector item paths | `vector/{year}/zone=NN/utmNN.json` | `vector/{year}/utmNN/utmNN.json` for those years |
| collection-level vector assets | 10 | 18 — `cells-a5r7` replaces `cells`, and `webcells-a5r4` … `webcells-a5r11` are new |
| grand total parcels | 1,242,648,258 | **1,773,302,757** |

The published copy is authoritative, so this branch adopted it: every tracked
metadata object was fetched from
`https://data.source.coop/ftw/global-data-2e/` and replaced its committed
version. 682 objects, every one size-checked against the bucket listing it
came from. The manifest:

```
tracked in git main : 640
published (adopted) : 682
identical           : 169     (all of 2017 and 2019, and the shared assets)
changed             :  93
only in git main    : 378     vector/{7 years}/zone=NN/utmNN.json
only published      : 420     vector/{7 years}/utmNN/utmNN.json
```

2017 and 2019 came back byte-identical, which is the same result this branch
had already proved by regenerating those two years. The 378 `zone=NN/utmNN.json`
item files are the previous generation's items for the seven re-processed
years. They are **still in the bucket** — publishing never deletes — but the
published collections do not link them, so they are orphans and this
repository no longer tracks them. Pruning them from the bucket is a separate
decision for the data owner.

### The generators cannot reproduce this tree, and what still holds

`tools/build_vector_items.py` and `tools/build_raster_items.py` describe the
previous generation. Do not run them over `catalog/` expecting a no-op:

- They read `index/vector.parquet` and `index/raster.parquet`, which now
  describe the current generation, so a run would rewrite every year's
  numbers, and they write items to `zone=NN/`, not `utmNN/`.
- They know nothing about the `webcells-a5r*` assets or `cells-a5r7`.
- `build_raster_items.py collections` derives its `child` links from the
  gitignored item tree, so a checkout without that tree emits year
  collections with no children at all.

These equivalence claims still hold, and were re-checked on this branch:

- The **2017 and 2019** vector collections, items, READMEs and AGENTS.md are
  what `build_vector_items.py` produces, byte for byte — the published copies
  and a generator run agree.
- The **2017 and 2019** raster collections, READMEs and AGENTS.md are what
  `build_raster_items.py collections` produces, byte for byte, when its child
  links are supplied from the committed tree.

These no longer hold, and are the work waiting on the upstream generator:

- Every other year's vector and raster metadata. The pipeline changes that
  produce it are in `taylor-geospatial/global-ftw-2e` and are to be
  upstreamed into this repository; no code was copied from there.

Until they are, the fields this repository owns are re-applied by
`tools/apply_v03_metadata.py`, which is idempotent and gated by
`tests/test_v03_metadata.py` — a `--check` run that fails when `catalog/`
stops matching what the script says it should be. That is what replaces
"edit the generator and re-run it" for the years the generators no longer
describe. The script is also the specification of what to upstream: every
field in it is one the published tree lacked.

Two generator traps were closed on the way, both of the "a re-run silently
drops metadata" kind that `tiles_meta_for` already guarded against:
`build_vector_items.py` reads the item `file:checksum` values back out of the
committed items when no `--checksums` sidecar is given, and
`build_raster_items.py` reads the `mirror` digest back out of the committed
collection when the staged `items.parquet` is absent.

### The published summary tables disagreed with the published collections

`catalog/README.md` and `catalog/vector/README.md` carry a
`| Year | Processing | Raster tiles | Predicted parcels |` table. As
published, its last column disagreed with the collections for four years:

| Year | Published table | Collection, year README and items | Difference |
|---|---:|---:|---:|
| 2018 | 207,772,743 | 207,793,034 | 20,291 |
| 2023 | 217,506,501 | 217,529,063 | 22,562 |
| 2024 | 204,609,812 | 204,629,903 | 20,091 |
| 2025 | 225,519,173 | 225,542,717 | 23,544 |

2020, 2021 and 2022 agree. The collections, the year READMEs and the item
`table:row_count` values all agree with each other, so the table is the
outlier; `tools/apply_v03_metadata.py` corrects it from the sum over the
items. `tests/test_counts.py` is the gate that found this, and it reads every
copy of every count back — 1,060 conditional checks on the current tree.
Reported for the pipeline that writes those tables; the cause is upstream, not
here.

### What `tests/test_counts.py` stopped requiring, and why

Two shapes of document are now in the tree. This repository's year README
leads with `**N parcels** in K per-UTM-zone GeoParquet files (G GiB)`; the
published one for a re-processed year leads with `N parcels in K UTM-zone
files` and has no GiB, no "largest zone" sentence, and an AGENTS.md with no
counts at all. The gate therefore checks each number **where it appears**
rather than requiring every document to state every number. What stayed
unconditional: the collection description's parcel count and GiB, the year
README's leading count, the summary-table rows, and the two repo-level grand
totals. What became conditional: the item `properties.description` counts,
the "largest zone" sentence, the AGENTS.md count, and the grand total in
`catalog/README.md`, `catalog/vector/README.md` and `catalog/catalog.json`,
none of which the published documents state any more. The gate prints how
many conditional checks ran, so a document quietly losing its numbers shows
up as that number falling.
