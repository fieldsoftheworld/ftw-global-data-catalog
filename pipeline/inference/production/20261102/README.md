# November 2026 production inference snapshot

These files are byte-for-byte copies of the inference runtime deployed for the
current 384-native-pixel production run. `provenance.json` and `SHA256SUMS` bind
that runtime and its environment. Keep this snapshot immutable; make a new
version for a changed recipe.

The general `../../stream.py` uses process writers. This deployed snapshot uses
thread writers and a different source-code fingerprint. The general dependency
lock also advances independently. Use this directory and its lock together when
continuing this production run; do not mix files or dependency locks.

See [the RAILS handoff](../../RAILS.md) for setup, sharding and output checks.
No crop mask, probability threshold, polygon extraction or hole filling runs
here. The COGs retain continuous field/boundary probabilities, quantized to
uint8 with scale 1/255. The 10% parcel score and under-100 m² hole filling belong
to subsequent vector postprocessing.

Verify the snapshot before installing or running it:

```bash
cd pipeline/inference/production/20261102
sha256sum -c SHA256SUMS
```

The numerical code hash is computed from `predict.py`, `nodata.py`, `stream.py`
and `qa.py` in that order, using the same filename/NUL/content/NUL framing as
`qa.code_sha256`. The model hash covers the ONNX file followed by its external
`.onnx.data` file, if present. Both expected hashes are in `provenance.json`.
A different Git commit for this snapshot is truthful provenance: the run
fingerprint excludes the commit and host, but includes source bytes, model,
library versions and numerical settings.

Local source checks do not establish GPU equivalence on a new machine. Run the
full golden self-test on each allocated GPU before accepting inference work.
