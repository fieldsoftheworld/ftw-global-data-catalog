# Portolan Conformance

Conformance means passing [rashid](https://github.com/portolan-sdi/rashid),
not claiming to conform, so it runs in CI:

```bash
python3 tests/test_portolan_conformance.py
```

That gate fails on any error-severity finding whose rule is not listed below
(at the paths listed). It must never grow without a row here. A known
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

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-LNK-006 | `raster/{year}/collection.json` (486 `child` links) | the 54 zone catalogs per year exist only in the bucket | none filed yet, see below |
| PTL-COL-005 | `raster/{year}/collection.json` (`mirror` asset) | the 7,466 item JSONs exist only in the bucket | none filed yet, see below |
| PTL-VIZ-002 | `raster/{year}/collection.json` (`overview` asset) | the raster tree has no MapLibre style | none filed yet, see below |

### The raster year collections are bucket snapshots

`catalog/raster/{year}/{README.md,AGENTS.md,collection.json,thumbnail.webp}` are copies of the
published objects. The published raster tree is hive-partitioned
(`raster/{year}/zone=ZZ/gzd=ZZL/{tile}/{tile}.{tif,json,thumb.png}`): each year collection links
54 `zone=ZZ/catalog.json` children, each splitting into `gzd=ZZL/catalog.json` catalogs whose
`item` links reach the 67,197 tile items. The tooling that generated those catalogs and items
is not in this repository (the earlier release tree was copied to 2e by a one-off relay that was not
committed), and committing ~11,000 catalog files whose items cannot be checked offline would not
make the gate meaningful. So the three rules above are waived for the nine year collections
only; the same findings anywhere else, including the vector tree, still fail. The gate asserts
the waiver's size (9 collections x 56 findings = 504) and that each year collection has exactly 54
zone links. The 486 zone-catalog links are only checked to exist **locally**: `tests/test_links.py`
HEAD-checks them against the bucket when `CI_LIGHT` is unset, but CI sets it, so CI never verifies
them. Run the gates without `CI_LIGHT` before merging. Remove these rows when the generator is brought into the repo (see CLAUDE.md) or when
the raster collections gain a style asset and in-repo items. Open a tracking issue first; there
is none yet.

<!--
When you accept one, add a row and a section explaining it, like this:

| Rule | Where | Why accepted | Tracking |
|---|---|---|---|
| PTL-VIZ-001 | all thumbnails | WebP is not yet permitted; the size saving is 4x | portolan-spec#121 |

Then add the rule id to ACCEPTED in tests/test_portolan_conformance.py. Both, or
neither.
-->

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
