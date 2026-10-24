import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


class SemanticOverrideCliTests(unittest.TestCase):
    def test_build_help_exposes_opt_in_override_file(self):
        result = subprocess.run(
            [sys.executable, str(PACKAGE / "src" / "main.py"), "build", "--help"],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("--semantic-overrides", result.stdout)

    def test_canonical_export_help_exposes_opt_in_override_file(self):
        result = subprocess.run(
            [sys.executable, str(PACKAGE / "src" / "export_canonical_graph.py"), "--help"],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("--semantic-overrides", result.stdout)

    def test_all_sources_reject_dataset_specific_override_file(self):
        result = subprocess.run(
            [sys.executable, str(PACKAGE / "src" / "main.py"), "build", "--all", "--semantic-overrides", "unused.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("single --input build", result.stderr)


if __name__ == "__main__":
    unittest.main()
