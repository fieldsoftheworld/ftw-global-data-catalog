# Parallel coverage simplification

Optional Rust alternative to GEOS 3.13.1 coverage simplification. Reads per-tile
EPSG:4326 GeoParquet, simplifies in the tile's UTM CRS, then updates geometry
and bounds. Row order, other columns and schema metadata are preserved.

```sh
cargo build --release --locked --manifest-path pipeline/coverage-simplify/Cargo.toml
pipeline/coverage-simplify/target/release/coverage-simplify \
  --year 2025 --in-root outlines --out-root simplified --threads 8 \
  --geos-lib /path/to/libgeos_c.so --proj-lib /path/to/libproj.so \
  --proj-data /path/to/share/proj
```

GEOS must be 3.13.1; reference projection used PROJ 9.5.1. Ensure transitive
native libraries are discoverable by the platform loader. Inputs and outputs
must have different roots. `--tiles`, `--tile-list`, `--shard`, `--num-shards`,
`--tolerance-m` (default 5), and `--audit-validator` select work and verification.
Writes use a temporary sibling file and atomic rename; unchanged inputs skip
using a tolerance/size/mtime fingerprint.

The validator, edge extraction and ordered simplifier run in Rust/Rayon.
PROJ performs projection; GEOS performs repair and the invalid-coverage fallback.
This is a GEOS source port, licensed LGPL-2.1-or-later. See LICENSE and NOTICE.

## Validation

```sh
cargo fmt --manifest-path pipeline/coverage-simplify/Cargo.toml --check
cargo clippy --locked --manifest-path pipeline/coverage-simplify/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path pipeline/coverage-simplify/Cargo.toml
```

174 golden geometry cases exercise ties, shared edges, holes and invalid input.
Native tests require Linux Shapely 2.1.2 and pyproj 3.7.2 wheels. Set
`NATIVE_SITE_PACKAGES` to their site-packages directory and add its `shapely.libs`
and `pyproj.libs` to `LD_LIBRARY_PATH`; run cargo test with `-- --include-ignored`.

Seven production tiles tested at 1/8/32 threads matched reference WKB exactly.
Three ~14-million-coordinate tiles took 7.35–9.07 s at 32 threads versus
115.96–124.42 s for serial Python/GEOS. These were single runs on shared Linux
CPU nodes, with uncontrolled cache state. They are observations, not guarantees.

Parity preserves defects in the reference outputs, including overlaps, lost
contacts and invalid stored geometry after inverse projection. This tool does
not guarantee valid polygon coverage. The adaptive orientation predicate also
differs from GEOS arithmetic; arbitrary extreme inputs may differ.
