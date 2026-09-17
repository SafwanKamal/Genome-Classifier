from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from software.variantgate.select_routing_threshold import (
    validate_run_manifest,
    validated_selection_frame,
)


class SelectionGuardTest(unittest.TestCase):
    def test_rejects_test_split_before_loading_files(self) -> None:
        with self.assertRaisesRegex(ValueError, "test split is prohibited"):
            validated_selection_frame(
                scores_path=Path("missing_scores.parquet"),
                dataset_path=Path("missing_dataset.parquet"),
                split="test",
            )

    def test_rejects_limited_validation_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "run_manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "input": {
                            "split": "validation",
                            "limit": 100,
                        },
                        "model": {
                            "export_manifest_sha256": "model-hash",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "limited triage run"):
                validate_run_manifest(
                    run_manifest_path=path,
                    split="validation",
                    model_manifest_sha256="model-hash",
                )

    def test_rejects_model_manifest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "run_manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "input": {
                            "split": "validation",
                            "limit": None,
                        },
                        "model": {
                            "export_manifest_sha256": "wrong-model",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "different models"):
                validate_run_manifest(
                    run_manifest_path=path,
                    split="validation",
                    model_manifest_sha256="expected-model",
                )


if __name__ == "__main__":
    unittest.main()
