from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from scripts.freeze_variantgate_eval import cases, freeze
from software.variantgate.brief_pipeline import (
    check_schema,
    run_briefs,
    validate_extraction,
    validate_synthesis,
)
from software.variantgate.evaluation_cli import evaluate
from software.variantgate.grounding import build_package
from software.variantgate.llm import (
    EXTRACTION_PROMPT,
    EXTRACTION_SCHEMA,
    ExtractiveBackend,
    JsonAPIBackend,
)
from software.variantgate.pipeline_cli import build_parser, run_pipeline


class GoldenBackend(ExtractiveBackend):
    """Scripted reference outputs; not a real model."""

    identity: ClassVar[dict] = {"provider": "scripted_test", "model": "golden-v1"}

    def __init__(self):
        self.calls = 0

    def generate(self, stage, payload, schema, prompt):
        self.calls += 1
        receipt = super().generate(stage, payload, schema, prompt)
        if stage == "extract" and payload["source"]["kind"].startswith("pubmed:"):
            source = payload["source"]
            gene = payload["subject"]["gene"]
            text = source["text"]
            claims = []
            for case in cases():
                if case["record"]["subject"]["gene"] == gene:
                    claims = [
                        {"statement": r["quote"], **r}
                        for r in case["reference"]["findings"]
                        if r["quote"] in text
                    ]
                    break
            receipt["data"]["claims"] = claims
        receipt["usage"] = {"input_tokens": 100, "output_tokens": 20}
        return receipt


class BriefGroundingTest(unittest.TestCase):
    def setUp(self):
        self.record = cases()[0]["record"]
        self.package = build_package(self.record, "GRCh38")
        self.source = next(
            s for s in self.package["sources"] if s["kind"].startswith("pubmed:")
        )
        self.output = {
            "source_id": self.source["source_id"],
            "limitations": ["abstract_only"],
            "claims": [
                {
                    "statement": self.source["text"],
                    "quote": self.source["text"],
                    "scope": "exact_variant",
                    "finding_type": "functional",
                }
            ],
        }

    def test_valid_exact_finding_preserves_review_boundary(self):
        value = validate_extraction(self.package, self.source, self.output)
        self.assertEqual(
            value["claims"][0]["semantic_support"], "requires_human_review"
        )

    def test_rejects_unknown_source(self):
        self.output["source_id"] = "invented"
        with self.assertRaisesRegex(ValueError, "wrong source"):
            validate_extraction(self.package, self.source, self.output)

    def test_rejects_fabricated_quote(self):
        self.output["claims"][0]["quote"] = (
            "Invented experiment demonstrated complete disease rescue."
        )
        with self.assertRaisesRegex(ValueError, "verbatim"):
            validate_extraction(self.package, self.source, self.output)

    def test_rejects_gene_only_promoted_to_exact(self):
        package = build_package(cases()[1]["record"], "GRCh38")
        source = package["sources"][1]
        self.output["source_id"] = source["source_id"]
        self.output["claims"][0]["quote"] = source["text"]
        with self.assertRaisesRegex(ValueError, "exact variant"):
            validate_extraction(package, source, self.output)

    def test_quote_not_whole_abstract_must_identify_variant(self):
        record = copy.deepcopy(self.record)
        for provider in ("pubmed", "direct_pubmed"):
            record["evidence"][provider]["articles"][0]["abstract"] += (
                " There was no additional functional experiment."
            )
        package = build_package(record, "GRCh38")
        source = package["sources"][1]
        self.output["source_id"] = source["source_id"]
        self.output["claims"][0]["quote"] = (
            "There was no additional functional experiment."
        )
        with self.assertRaisesRegex(ValueError, "exact variant"):
            validate_extraction(package, source, self.output)

    def test_rejects_literature_as_database_statement(self):
        self.output["claims"][0]["finding_type"] = "database_statement"
        with self.assertRaisesRegex(ValueError, "separate"):
            validate_extraction(self.package, self.source, self.output)

    def test_rejects_extra_fields_and_invalid_categories(self):
        for mutation in (
            {**self.output, "classification": "pathogenic"},
            {**self.output, "limitations": ["made_up"]},
        ):
            with self.assertRaises(ValueError):
                check_schema(mutation, EXTRACTION_SCHEMA)

    def test_rejects_package_tampering(self):
        self.package["subject"]["gene"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_extraction(self.package, self.source, self.output)

    def test_source_ids_stable_and_articles_deduplicated(self):
        self.assertEqual(len(self.package["sources"]), 2)
        self.assertEqual(self.source["memberships"], ["direct_pubmed", "pubmed"])
        package = build_package(self.record, "GRCh38")
        self.assertEqual(package["sources"][1]["source_id"], self.source["source_id"])

    def test_missing_abstract_is_not_replaced_with_title(self):
        package = build_package(cases()[3]["record"], "GRCh38")
        self.assertEqual(len(package["sources"]), 1)
        self.assertEqual(package["gaps"]["missing_abstract_pmids"], ["90000004"])

    def test_truncation_is_explicit(self):
        package = build_package(self.record, "GRCh38", max_passage_chars=24)
        self.assertTrue(package["sources"][1]["truncated"])

    def test_short_protein_change_without_transcript_stays_context(self):
        record = copy.deepcopy(self.record)
        for provider in ("pubmed", "direct_pubmed"):
            article = record["evidence"][provider]["articles"][0]
            article["abstract"] = article["abstract"].replace("NM_900000001.1 ", "")
        package = build_package(record, "GRCh38")
        self.assertEqual(package["sources"][1]["identity_scope"], "gene_context")

    def test_conflicting_duplicate_abstracts_are_excluded(self):
        record = copy.deepcopy(self.record)
        record["evidence"]["direct_pubmed"]["articles"][0]["abstract"] = (
            "A different retrieved abstract."
        )
        package = build_package(record, "GRCh38")
        self.assertEqual(len(package["sources"]), 1)
        self.assertEqual(package["gaps"]["conflicting_abstract_pmids"], ["90000001"])

    def test_identity_blocks_wrong_gene_and_coordinate(self):
        for index in (5, 6):
            self.assertEqual(
                build_package(cases()[index]["record"], "GRCh38")["identity"]["status"],
                "blocked",
            )

    def test_unknown_assembly_cannot_verify_coordinates(self):
        self.assertEqual(build_package(self.record)["identity"]["status"], "blocked")

    def test_gene_agreement_alone_does_not_verify_variant(self):
        record = copy.deepcopy(self.record)
        record["evidence"]["clinvar"]["canonical_spdi"] = []
        self.assertEqual(
            build_package(record, "GRCh38")["identity"]["status"], "blocked"
        )

    def test_source_id_mismatch_and_indels_blocked(self):
        record = copy.deepcopy(self.record)
        record["evidence"]["clinvar"]["variation_id"] = 1
        self.assertIn(
            "clinvar_variation_id_mismatch",
            build_package(record, "GRCh38")["identity"]["issues"],
        )
        record = copy.deepcopy(self.record)
        record["subject"]["variant_key"] = "1:101:AA:A"
        self.assertEqual(
            build_package(record, "GRCh38")["identity"]["status"], "blocked"
        )

    def test_database_key_requires_a_source_uid_not_a_copied_request_id(self):
        record = copy.deepcopy(self.record)
        record["subject"]["variant_key"] = "VCV000010001"
        self.assertEqual(build_package(record)["identity"]["status"], "blocked")
        record["evidence"]["clinvar"]["source_variation_id"] = 10001
        self.assertEqual(build_package(record)["identity"]["status"], "verified")
        record["evidence"]["clinvar"]["source_variation_id"] = 12345
        self.assertEqual(build_package(record)["identity"]["status"], "blocked")

    def test_synthesis_rejects_omission_invention_duplicate_and_scope_change(self):
        claims = validate_extraction(self.package, self.source, self.output)["claims"]
        good = {
            s: []
            for s in (
                "database_statements",
                "variant_findings",
                "gene_context",
                "unresolved",
            )
        }
        good["variant_findings"] = [claims[0]["claim_id"]]
        self.assertEqual(validate_synthesis(claims, good), good)
        for value in (
            {**good, "variant_findings": []},
            {**good, "variant_findings": ["invented"]},
            {**good, "variant_findings": good["variant_findings"] * 2},
            {**good, "variant_findings": [], "gene_context": good["variant_findings"]},
        ):
            with self.assertRaises(ValueError):
                validate_synthesis(claims, value)


class BriefExecutionTest(unittest.TestCase):
    def test_database_and_grouping_never_call_model(self):
        class LiteratureOnlyBackend(GoldenBackend):
            def generate(self, stage, payload, schema, prompt):
                self_test.assertEqual(stage, "extract")
                self_test.assertTrue(payload["source"]["kind"].startswith("pubmed:"))
                return super().generate(stage, payload, schema, prompt)

        self_test = self
        with tempfile.TemporaryDirectory() as temp:
            root, backend = Path(temp), LiteratureOnlyBackend()
            summary = run_briefs([cases()[0]["record"]], root, backend, "GRCh38")
            self.assertEqual(backend.calls, 1)
            self.assertEqual(summary["failed_variant_number"], 0)
            brief = json.loads((root / "variant_briefs.jsonl").read_text())
            self.assertEqual(len(brief["sections"]["database_statements"]), 1)
            self.assertEqual(len(brief["sections"]["variant_findings"]), 1)
            database = brief["claims"][0]
            self.assertEqual(
                database["generation_method"], "structured_database_fields"
            )
            self.assertIn("Uncertain significance", database["statement"])
            self.assertEqual(database["quote"], brief["sources"][0]["text"])

    def test_database_only_brief_succeeds_with_zero_model_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = GoldenBackend()
            summary = run_briefs(
                [cases()[4]["record"]], Path(temp), backend, "GRCh38", max_calls=0
            )
            self.assertEqual(backend.calls, 0)
            self.assertEqual(summary["failed_variant_number"], 0)

    def test_frozen_corpus_scripted_reference_and_accounting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze(root / "corpus", None)
            summary = run_briefs(
                [c["record"] for c in cases()],
                root / "run",
                GoldenBackend(),
                "GRCh38",
                input_price=1,
                output_price=2,
            )
            self.assertEqual(summary["blocked_identity_number"], 6)
            self.assertEqual(summary["failed_variant_number"], 0)
            self.assertGreater(summary["accounting"]["estimated_cost_usd"], 0)
            evaluation = evaluate(
                root / "corpus/synthetic_cases.jsonl",
                root / "run/variant_briefs.jsonl",
                root / "run/summary.json",
            )
            self.assertEqual(evaluation["status"], "pass")
            self.assertEqual(evaluation["reference_finding_coverage"], 1)
            self.assertIsNone(evaluation["semantic_support_accuracy"])

    def test_blocked_identity_never_calls_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = GoldenBackend()
            summary = run_briefs([cases()[5]["record"]], Path(temp), backend, "GRCh38")
            self.assertEqual(backend.calls, 0)
            self.assertEqual(summary["status"], "attention_required")

    def test_resume_reuses_calls_and_revalidates_cached_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backend = GoldenBackend()
            run_briefs([cases()[0]["record"]], root, backend, "GRCh38")
            prior = backend.calls
            summary = run_briefs([cases()[0]["record"]], root, backend, "GRCh38")
            self.assertEqual(backend.calls, prior)
            self.assertEqual(summary["accounting"]["new_generation_calls"], 0)
            cache = next((root / "llm_responses").glob("*.json"))
            value = json.loads(cache.read_text())
            value["receipt"]["usage"] = {"input_tokens": 999}
            cache.write_text(json.dumps(value))
            summary = run_briefs([cases()[0]["record"]], root, backend, "GRCh38")
            self.assertEqual(summary["failed_variant_number"], 1)

    def test_budget_failure_can_resume_remaining_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root, backend = Path(temp), GoldenBackend()
            summary = run_briefs(
                [cases()[0]["record"]], root, backend, "GRCh38", max_calls=0
            )
            self.assertEqual(summary["failed_variant_number"], 1)
            self.assertEqual((root / "variant_briefs.jsonl").read_text(), "")
            summary = run_briefs(
                [cases()[0]["record"]], root, backend, "GRCh38", max_calls=1
            )
            self.assertEqual(summary["failed_variant_number"], 0)
            self.assertEqual(backend.calls, 1)

    def test_rejected_response_usage_retained_without_partial_brief(self):
        class BadBackend(GoldenBackend):
            def generate(self, stage, payload, schema, prompt):
                receipt = super().generate(stage, payload, schema, prompt)
                if stage == "extract":
                    receipt["data"]["source_id"] = "wrong"
                return receipt

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_briefs([cases()[0]["record"]], root, BadBackend(), "GRCh38")
            self.assertEqual(result["accounting"]["input_tokens"], 100)
            self.assertEqual(result["brief_number"], 0)
            self.assertEqual(result["failed_variant_number"], 1)

    def test_pipeline_resume_and_input_hash_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze(root / "corpus", None)
            command = [
                "--reconciled",
                str(root / "corpus/synthetic_evidence.jsonl"),
                "--assembly",
                "GRCh38",
                "--limit",
                "24",
                "--output-dir",
                str(root / "run"),
            ]
            args = build_parser().parse_args(command)
            run_pipeline(args)
            args.resume = True
            result = run_pipeline(args)
            self.assertEqual(result["accounting"]["new_generation_calls"], 0)
            evidence = root / "corpus/synthetic_evidence.jsonl"
            evidence.write_text(evidence.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                run_pipeline(args)

    def test_evaluation_rejects_changed_corpus(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze(root / "corpus", None)
            path = root / "corpus/synthetic_cases.jsonl"
            path.write_text(path.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                evaluate(path, Path("unused"), Path("unused"))


class ProviderTest(unittest.TestCase):
    def test_local_never_reads_openai_key_and_requires_loopback(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret-for-openai"}):
            backend = JsonAPIBackend("local", "test")
            self.assertEqual(backend.key, "")
            with self.assertRaises(ValueError):
                JsonAPIBackend("local", "test", "http://remote.example/v1")
            with self.assertRaises(ValueError):
                JsonAPIBackend("local", "test", "http://localhost/v1?key=secret")

    def test_responses_request_schema_refusal_and_usage(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.payload).encode()

        output = {"source_id": "source", "claims": [], "limitations": []}
        response = {
            "status": "completed",
            "model": "explicit-model",
            "id": "response-1",
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "output": [
                {"content": [{"type": "output_text", "text": json.dumps(output)}]}
            ],
        }
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            backend = JsonAPIBackend("openai", "explicit-model")
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.return_value = Response(response)
                receipt = backend.generate(
                    "extract", {}, EXTRACTION_SCHEMA, EXTRACTION_PROMPT
                )
                request = opener.return_value.open.call_args.args[0]
                body = json.loads(request.data)
                self.assertFalse(body["store"])
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertEqual(receipt["data"], output)
                self.assertEqual(receipt["usage"]["input_tokens"], 10)
                response["status"] = "incomplete"
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    backend.generate(
                        "extract", {}, EXTRACTION_SCHEMA, EXTRACTION_PROMPT
                    )
                response["status"] = "completed"
                response["output"] = [
                    {"content": [{"type": "refusal", "refusal": "no"}]}
                ]
                with self.assertRaisesRegex(ValueError, "refused"):
                    backend.generate(
                        "extract", {}, EXTRACTION_SCHEMA, EXTRACTION_PROMPT
                    )

    def test_local_chat_request_and_nonstop_rejection(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {"finish_reason": "length", "message": {"content": "{}"}}
                        ]
                    }
                ).encode()

        backend = JsonAPIBackend("local", "test")
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                backend.generate("extract", {}, EXTRACTION_SCHEMA, EXTRACTION_PROMPT)
            request = opener.return_value.open.call_args.args[0]
            self.assertNotIn("Authorization", request.headers)
            body = json.loads(request.data)
            self.assertIn("response_format", body)
            self.assertEqual(body["temperature"], 0)
            self.assertIn(
                json.dumps(EXTRACTION_SCHEMA, separators=(",", ":")),
                body["messages"][0]["content"],
            )
            self.assertEqual(backend.identity["prompt_format"], "schema-in-prompt-v1")


if __name__ == "__main__":
    unittest.main()
