from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.freeze_variantgate_eval import cases
from software.variantgate.evidence.clinvar import ClinVarSummary
from software.variantgate.manifest import sha256_file
from software.variantgate.pipeline_cli import build_parser, run_pipeline
from software.variantgate.tests.test_direct_cli import FakePubMedClient


class SyntheticClinVarClient:
    calls = 0

    def __init__(self, cache_directory, **kwargs):
        self.cache_directory = cache_directory.resolve()
        self.api_key_used = False
        self.request_attempt_number = self.successful_request_number = 1

    def fetch_many(self, ids):
        type(self).calls += 1
        cv = cases()[0]["record"]["evidence"]["clinvar"]
        raw = {
            "uid": str(cv["variation_id"]),
            "title": cv["title"],
            "genes": cv["genes"],
            "protein_change": cv["protein_change"],
            "germline_classification": {
                "description": "Uncertain significance",
                "trait_set": [],
            },
            "variation_set": [{"canonical_spdi": cv["canonical_spdi"][0]}],
        }
        return {
            i: ClinVarSummary(i, "2026-01-01", "https://example.invalid", False, raw)
            for i in ids
        }


class SyntheticPubMedClient(FakePubMedClient):
    fail_once = False
    link_calls = 0

    def fetch_links(self, ids):
        type(self).link_calls += 1
        if type(self).fail_once:
            type(self).fail_once = False
            raise RuntimeError("Injected network failure")
        return {
            i: {
                "pmids": ["123"],
                "retrieved_at": "2026-01-01",
                "request_url": "https://example.invalid",
                "cache_hit": False,
                "raw_response_sha256": "test-hash",
            }
            for i in ids
        }

    def fetch_abstracts(self, pmids):
        values = super().fetch_abstracts(pmids)
        abstract = cases()[0]["record"]["evidence"]["direct_pubmed"]["articles"][0][
            "abstract"
        ]
        for value in values.values():
            value["content"]["abstract"] = abstract
        return values


class FullPipelineTest(unittest.TestCase):
    def arguments(self, root):
        features = [f"feature_{i}" for i in range(16)]
        manifest = {
            "source_model_sha256": "test-model",
            "architecture": "16-1-1",
            "feature_order": features,
            "qshift": 4,
            "classification_rule": "folded_score >= 0",
            "original_validation_threshold": 0,
            "folded_output_bias": 1,
            "hidden_weights": [[0] * 16],
            "hidden_biases": [0],
            "output_weights": [0],
        }
        model = root / "model.json"
        model.write_text(json.dumps(manifest))
        policy = root / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "policy_name": "test-policy",
                    "rule": {
                        "threshold": 0,
                        "deep_review": "score >= threshold",
                        "light_review": "score < threshold",
                    },
                    "selection": {
                        "split": "validation",
                        "target_pathogenic_recall": 0.995,
                        "achieved_pathogenic_recall": 1.0,
                    },
                    "model": {
                        "architecture": "16-1-1",
                        "source_model_sha256": "test-model",
                        "export_manifest_sha256": sha256_file(model),
                    },
                }
            )
        )
        variant = cases()[0]["record"]["subject"]
        input_path = root / "variants.parquet"
        pd.DataFrame(
            [
                {
                    **{k: 0 for k in features},
                    **variant,
                    "variation_id": variant["clinvar_variation_id"],
                }
            ]
        ).to_parquet(input_path)
        return build_parser().parse_args(
            [
                "--input",
                str(input_path),
                "--manifest",
                str(model),
                "--routing-policy",
                str(policy),
                "--assembly",
                "GRCh38",
                "--limit",
                "1",
                "--output-dir",
                str(root / "run"),
                "--cache-dir",
                str(root / "cache"),
            ]
        )

    def patches(self):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(
            patch(
                "software.variantgate.evidence_cli.ClinVarClient",
                SyntheticClinVarClient,
            )
        )
        stack.enter_context(
            patch("software.variantgate.pubmed_cli.PubMedClient", SyntheticPubMedClient)
        )
        stack.enter_context(
            patch(
                "software.variantgate.direct_pubmed_cli.PubMedClient",
                SyntheticPubMedClient,
            )
        )
        return stack

    def test_real_cpu_through_all_stages_with_mocked_ncbi_and_resume(self):
        with tempfile.TemporaryDirectory() as temp, self.patches():
            root = Path(temp)
            args = self.arguments(root)
            result = run_pipeline(args)
            self.assertEqual(result["status"], "pass")
            brief = json.loads((root / "run/briefs/variant_briefs.jsonl").read_text())
            self.assertEqual(brief["triage"]["score"], 1)
            self.assertEqual(len(brief["sources"]), 2)
            self.assertTrue(brief["sources"][1]["text"])
            calls = SyntheticClinVarClient.calls
            args.resume = True
            resumed = run_pipeline(args)
            self.assertEqual(resumed["accounting"]["new_generation_calls"], 0)
            self.assertEqual(SyntheticClinVarClient.calls, calls)
            state = json.loads((root / "run/pipeline_state.json").read_text())
            completed_output = Path(state["stages"]["clinvar"]["result"])
            completed_output.write_text(completed_output.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "modified or removed"):
                run_pipeline(args)

    def test_failed_retrieval_resumes_without_repeating_completed_stages(self):
        with tempfile.TemporaryDirectory() as temp, self.patches():
            root = Path(temp)
            args = self.arguments(root)
            SyntheticPubMedClient.fail_once = True
            with self.assertRaises(RuntimeError):
                run_pipeline(args)
            state = json.loads((root / "run/pipeline_state.json").read_text())
            self.assertEqual(set(state["stages"]), {"triage", "clinvar"})
            prior = SyntheticClinVarClient.calls
            args.resume = True
            result = run_pipeline(args)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(SyntheticClinVarClient.calls, prior)


if __name__ == "__main__":
    unittest.main()
