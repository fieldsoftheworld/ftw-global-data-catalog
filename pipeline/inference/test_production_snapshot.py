"""Keep the portable production runtime bound to its deployed byte identities."""

import ast
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parent / "production" / "20261102"
CODE_SHA = "876718a6d18bc77cbc8bcb36eefbcd9cf7e06deb8e2173ccd1412414c61d9820"


class ProductionSnapshotTest(unittest.TestCase):
    def test_deployed_file_identities(self):
        manifest = json.loads((ROOT / "provenance.json").read_text())
        sums = {}
        for line in (ROOT / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split()
            sums[name] = digest
        self.assertEqual(sums, manifest["files"])
        for name, expected in sums.items():
            with self.subTest(name=name):
                data = (ROOT / name).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected)
                if name.endswith(".py"):
                    ast.parse(data)

    def test_production_numerical_fingerprint(self):
        digest = hashlib.sha256()
        for name in ("predict.py", "nodata.py", "stream.py", "qa.py"):
            digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes() + b"\0")
        self.assertEqual(digest.hexdigest(), CODE_SHA)
        manifest = json.loads((ROOT / "provenance.json").read_text())
        self.assertEqual(manifest["inference_code_sha256"], CODE_SHA)


if __name__ == "__main__":
    unittest.main()
