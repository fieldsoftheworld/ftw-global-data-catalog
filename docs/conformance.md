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
all**, and the only error-severity rule left is PTL-VIZ-002 (9×, the waiver
above), so the gate passes with `ACCEPTED` exactly `{"PTL-VIZ-002"}`. The
measurements that chose that shape are kept below, because they also bound
what may and may not be changed about it later.

### The generated item tree is not committed (user ruling 2026-10-03)

The tree is **generated, not authored**: `tools/build_raster_items.py items`
rebuilds all 78,267 files (67,197 items + 3,690 zone/GZD catalogs) from
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
  `ACCEPTED` stays exactly `{"PTL-VIZ-002"}` — no finding is waived,
  because no run that could produce those findings is accepted as valid.
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
  plain catalogs and not only collections (measured). That is 11,070
  generated documentation files.
- **Warnings, not errors.** Measured over the committed tree: 1,497
  PTL-CAT-001 (1,494 GZD leaves holding 20 or more tiles, plus the 3 vector
  collections that already warned) and 201,615 PTL-AST-003 (134,415 "no
  `file:checksum`" and 67,200 "no `file:size`"). The year collections
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
| **grouped, 9 years (this catalog)** | **71,088** | **3,703** | **2 h 30 min** |

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
