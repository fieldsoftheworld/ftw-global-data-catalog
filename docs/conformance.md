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

The gate needs rashid `>=0.1.8,<0.2.0`. It reads `rashid --version` and fails
outside that range. It also fails when rashid is absent, and prints the install
command. A skip would report a green run for a catalog that no validator read.

The floor is 0.1.8 because it is the first rashid that accepts a Portolan
v0.2.0 root `self` link (v0.1.1 forbids that link and v0.2.0 recommends it,
PORTO-CORE-081). This catalog declares v0.2.0, so an older rashid reports
errors on a correct catalog. The same range is in the CI install step.

The upper bound stops an unreviewed 0.2 rule set from changing what this gate
means. Raise both bounds together when you move to 0.2, and read the new rules
first.

This file also records workarounds for the other validator CI runs. Those are
not conformance debts, because the catalog is correct and the validator is not.
They live here so nobody has to read CI code to find out why a gate skips
something.

## Accepted deviations

None.

<!--
When you accept one, add a row and a section explaining it, like this:

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-VIZ-001 | all thumbnails | WebP is not yet permitted; the size saving is 4x | portolan-spec#121 |
| PTL-VIZ-002 | `raster/{2017..2025}/collection.json` | The visualization derivative is a pre-rendered RGB JPEG COG (roles `visual`,`overview`,`cloud-optimized`); its styling is baked into the pixels at build time, so no client-side style document exists for a `style` asset to name. The colormap is documented in each README and in `pipeline/make_overview.py`. | rashid#202 |

Then add the rule id to ACCEPTED in tests/test_portolan_conformance.py. Both, or
neither.
-->

## Policy: no `file:checksum` on the raster COG assets

The raster items carry `file:size` (from `index/raster.parquet`) and no
`file:checksum`. PORTO-CORE-029 wants one; rashid reports its absence as a
**warning** (PTL-AST-003), not an error, so no `ACCEPTED` entry is needed and
the gate stays honest.

It is a deliberate policy, not an oversight. A multihash checksum means
reading the bytes, and the raster tree is 67,197 COGs totalling ~26 TB. The
index carries no checksum column (measured: 14 columns, none of them a
digest), so filling the field would mean streaming 26 TB — nine times the
whole vector tree — to add a field that no reader of this catalog has asked
for. The vector tree, at 227 GiB in 108 files, was cheap enough to hash
(`tools/hash_remote.py`) and does carry checksums. The rule this follows is
the s2-stac-geoparquet one: checksums where they are cheap.

`tools/build_raster_items.py items` reads a `--sidecar` for header facts and
fills `file:checksum` the moment a digest is available for a tile, so the
policy reverses by producing the digests, not by editing the generator.

The same reasoning covers two collection-level assets: the per-year
`overview.tif` (a multi-GB mosaic) carries `file:size` from its HEAD response
and no checksum. The per-year `thumbnail.webp` is small, so the generator
downloads it and carries both.

## Open: the raster collections publish no item connectivity (PTL-COL-005)

Each year collection has ~7,466 items. They are generated straight to S3 and
not committed (docs/plan.md Phase 4), and the collections carry no `rel: item`
link, so enumeration goes through `index/raster.parquet` and each collection's
`items.parquet` collection-mirror. rashid 0.1.8 reports that as an **error**,
nine times:

```
PTL-COL-005  raster/{2017..2025}/collection.json: collection registers item
mirror 'mirror' but publishes no items; the mirror is a derived copy and the
item JSON remains the normative representation
(PORTO-CORE-015, PORTO-CORE-032, PORTO-FMT-042)
```

This is **not** an accepted deviation and not a validator artifact. The
finding is correct: PORTO-CORE-032 wants "a `child` or `item` link for every
object it contains", and 67,197 published items have none. It is recorded
here as an open item, with the measurements that bound the repair, so the
next session does not redo them.

### What a grouped browse subcatalog tree can and cannot do

The obvious repair — group the items under browse subcatalogs instead of
listing them flat — was measured against rashid 0.1.8 and **does not work
with the published layout**, because rashid derives containment from
directory nesting (`CatalogGraph.parent_of` walks up from a file's own
directory to the nearest `catalog.json`/`collection.json`). Measured, on
scratch copies of this catalog:

| Tried | Result |
|---|---|
| Browse catalogs committed under `raster/{year}/browse/{zone}/`, `rel: item` links (relative **or** absolute under the published base) to the bucket-side items | PTL-COL-005 **stays** (9×) *and* one new PTL-LNK-006 error per item link — "href … does not resolve to any file" — i.e. 67,197 new errors |
| The same, with the item JSONs present on disk at `raster/{year}/{tile}/{tile}.json` | PTL-COL-005 clears, but the browse catalogs' item links become PTL-LNK-006 "points to the wrong object: must point to an item contained by this object", and PTL-LNK-002 demands a `rel: item` link on the **collection** for every item instead |

So a browse subcatalog can only own items that live **inside its own
directory**, and the item JSON must be in the checkout for rashid to see it
at all. `PTL-CAT-001`'s threshold is 20 ungrouped children (`PORTO-CORE-078`,
a SHOULD, so a warning), which no flat list of 7,466 can meet.

### The two layouts that do clear it

Both were built for 2017 and run through rashid; both clear PTL-COL-005 for
that year with **no link errors**. Both require the item JSONs in git
(measured: 67,197 items, 288 MiB on disk, ~16 MB packed — one year's 7,466
items are 32 MiB raw and 1.77 MB gzipped, since the items are near-identical).

1. **Flat, layout unchanged.** Items committed at
   `catalog/raster/{year}/{tile}/{tile}.json` — the keys they already occupy
   in the bucket — and ~7,466 `rel: item` links per collection
   (`collection.json` grows to 1.11 MiB). No bucket relayout, no new keys.
   Leaves one PTL-CAT-001 **warning** per year collection ("7466 children
   with no subcatalog grouping them"), the same warning the vector
   collections already carry at 54 children.
2. **Grouped, item keys only.** Items move to
   `raster/{year}/zone-{NN}/{GZD}/{tile}/{tile}.json`, reaching their COG and
   thumbnail — which never move — with an upward relative href
   (`../../../{tile}/{tile}.tif`). Per year: 54 zone catalogs → 356 GZD
   catalogs → items, median 17 and max 63 per leaf. 486 zone + 3,204 GZD
   catalogs across nine years, each needing README.md and AGENTS.md
   (PTL-FIL-001/002/003 bind plain catalogs too, measured). Clears the
   collection-level PTL-CAT-001; leaves 166 leaf warnings per year where a
   GZD holds 20 or more tiles. Costs 288 MiB of item JSON re-uploaded to new
   keys, leaves the old item keys stale, and walks back part of the
   2026-10-01 per-item-folder ruling (the item no longer sits beside its COG).

Either way, committing the items adds ~201,000 PTL-AST-003 **warnings**
(three per item: no `data` checksum, no `thumbnail` checksum, no `thumbnail`
`file:size`) — see the checksum policy above; the thumbnail `file:size` is
fillable from the recursive bucket listing the item uploader already does.

Gate runtime is the other cost, and it separates the two. rashid's
containment helpers cost (nodes × catalog-or-collection nodes):
`children_of` calls `parent_of` once per node, and `PTL-LNK-002` calls
`children_of` once per catalog. Measured on this machine: layout 1 at one
year is 7,658 files in **32 s** and at three years 22,590 files in **95 s**
— linear, because the structural-node count stays at 13, so nine years
projects to ~5 minutes. Layout 2 at one year is 8,068 files in **124 s**,
4× slower for 5% more files, because its 410 group catalogs each trigger a
full-tree scan; nine years (≈71,700 nodes, ≈3,700 catalogs) projects to
**~2.7 hours**, which no CI gate can carry. Either an upstream rashid
perf fix or layout 1 is needed.

`tools/publish.py` would also need the recursive-listing treatment
`tools/upload_data.py` already got, for either layout: it lists each catalog
directory non-recursively, which with 67,197 item directories is 67,197
list calls.

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

### portolan check --live HEADs partition-glob asset hrefs literally

The year collections carry a `data` asset whose href is the partition glob
(`./zone=*/utm*.parquet`) beside `partition:glob`, as the partition
extension defines. `portolan check --live` HEAD-requests that href
literally, and a `*` URL returns no usable Content-Length, so PTL-LIV-002
reports one error per collection. The catalog is correct — rashid conforms,
and every partition member is HEAD-verified individually through its item's
own data asset (tests/test_links.py). Tracked upstream:
https://github.com/portolan-sdi/portolan-cli/issues/914;
remove this section when the checker learns to expand or skip glob hrefs.
