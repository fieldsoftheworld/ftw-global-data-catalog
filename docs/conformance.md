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

## Policy: no `rel: item` links on the raster collections

Each year collection has ~7,466 items. The items are generated straight to
S3 and not committed (docs/plan.md Phase 4), so committing 7,466 item links
per year — ~10 MB of JSON across the nine years, in git forever — would
contradict that ruling to buy a flat list that PTL-CAT-001 already calls
hard to browse at 54 entries. Enumeration goes through
`index/raster.parquet` and each collection's `items.parquet`
collection-mirror instead, and both collection AGENTS.md files say so. rashid
reports nothing for this; a browse-subcatalog tree (docs/plan.md Phase 4.1)
is the open option if browsing the items in the data browser becomes a
requirement.

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
