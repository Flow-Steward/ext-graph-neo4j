"""Validate pinned native release provenance, architecture, and safety patch contracts."""

import hashlib
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeBuildTests(unittest.TestCase):
    def test_pinned_source_and_patch(self):
        provenance = json.loads((ROOT / "toolbox-provenance.json").read_text())
        self.assertEqual(provenance["tag"], "v1.16.0")
        self.assertEqual(provenance["commit"], "06ea49d9a487a72c22419b5a8d5c22c11f0d7485")
        self.assertIn("@sha256:", provenance["go_image"])
        for entry in (provenance, provenance["driver"]):
            self.assertEqual(
                hashlib.sha256((ROOT / entry["patch"]).read_bytes()).hexdigest(),
                entry["patch_sha256"],
            )
        self.assertEqual(provenance["driver"]["version"], "v6.4.0")
        self.assertEqual(
            provenance["driver"]["module_sum"],
            "h1:ffYRs1oVWvFDN51qu6ucUwsuPN2eWDls4q7j0PV3zBM=",
        )
        self.assertEqual(
            set(provenance["packages"]),
            {
                "github.com/googleapis/mcp-toolbox/internal/sources/neo4j",
                "github.com/googleapis/mcp-toolbox/internal/tools/neo4j/neo4jcypher",
            },
        )
        self.assertEqual(set(provenance["binaries"]), {"linux-amd64", "linux-arm64"})
        for target, details in provenance["binaries"].items():
            binary = (ROOT / "bin" / target / "toolbox").read_bytes()
            self.assertEqual(binary[:4], b"\x7fELF")
            self.assertEqual(binary[4:6], b"\x02\x01")
            self.assertEqual(
                struct.unpack_from("<H", binary, 18)[0], 62 if target.endswith("amd64") else 183
            )
            self.assertEqual(len(binary), details["size"])
            self.assertEqual(hashlib.sha256(binary).hexdigest(), details["sha256"])

    def test_minimal_import_build(self):
        script = ((ROOT / "scripts/build_toolbox.sh").read_text() + (ROOT / "scripts/build_inside.sh").read_text())
        self.assertIn("= 2", script)
        self.assertIn("sources/neo4j", script)
        self.assertIn("neo4jcypher", script)
        self.assertIn("CGO_ENABLED=0", script)
        self.assertIn("go test -p 1 ./internal/sources/neo4j", script)
        self.assertIn("go test -p 1 ./neo4j/internal/connector", script)
        self.assertIn("TestFlowSteward|TestDechunkerWithTimeout", script)


if __name__ == "__main__":
    unittest.main()
