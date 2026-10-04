from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from software.variantgate.repair_identifiers_cli import repair


class RepairIdentifiersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input.parquet"
        self.vcf = self.root / "source.vcf"
        self.output = self.root / "corrected.parquet"
        pd.DataFrame(
            {
                "variant_key": ["1:101:A:G"],
                "gene": ["TEST"],
                "variation_id": ["999"],
                "label": [1],
                "split": ["test"],
                "feature": [-127],
            }
        ).to_parquet(self.source)
        self.vcf.write_text(
            "##reference=GRCh38\n1\t101\t123\tA\tG\t.\tPASS\tALLELEID=999;GENEINFO=TEST:1\n"
        )

    def test_changes_only_ids_and_preserves_original(self):
        original = self.source.read_bytes()
        result = repair(self.source, self.vcf, self.output)
        corrected = pd.read_parquet(self.output)
        self.assertEqual(corrected.loc[0, "variation_id"], "123")
        self.assertEqual(corrected.loc[0, "allele_id"], "999")
        self.assertEqual(corrected.loc[0, "feature"], -127)
        self.assertTrue(result["features_labels_splits_unchanged"])
        self.assertEqual(self.source.read_bytes(), original)

    def test_refuses_overwrite(self):
        with self.assertRaisesRegex(ValueError, "new file"):
            repair(self.source, self.vcf, self.source)

    def test_rejects_wrong_gene_and_missing_variant(self):
        self.vcf.write_text(self.vcf.read_text().replace("TEST:1", "OTHER:1"))
        with self.assertRaisesRegex(ValueError, "gene mismatch"):
            repair(self.source, self.vcf, self.output)
        self.vcf.write_text(self.vcf.read_text().replace("101", "201"))
        with self.assertRaisesRegex(ValueError, "lacks"):
            repair(self.source, self.vcf, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_ambiguous_source_and_unrelated_legacy_id(self):
        self.vcf.write_text(self.vcf.read_text() * 2)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            repair(self.source, self.vcf, self.output)
        self.vcf.write_text(
            "1\t101\t123\tA\tG\t.\tPASS\tALLELEID=456;GENEINFO=TEST:1\n"
        )
        with self.assertRaisesRegex(ValueError, "Legacy ID"):
            repair(self.source, self.vcf, self.output)

    def test_rejects_explicit_wrong_assembly(self):
        self.vcf.write_text(self.vcf.read_text().replace("GRCh38", "GRCh37"))
        with self.assertRaisesRegex(ValueError, "assembly"):
            repair(self.source, self.vcf, self.output)


if __name__ == "__main__":
    unittest.main()
