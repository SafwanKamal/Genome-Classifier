"""Single-command CPU triage/retrieval or cached evidence to a resumable brief."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from software.variantgate.brief_pipeline import BRIEF_PIPELINE_VERSION, run_briefs
from software.variantgate.evidence.pubmed import write_json
from software.variantgate.grounding import digest
from software.variantgate.llm import (
    EXTRACTION_PROMPT,
    EXTRACTION_SCHEMA,
    ExtractiveBackend,
    JsonAPIBackend,
)
from software.variantgate.manifest import sha256_file


def read_evidence(path: Path, manifest: Path | None = None) -> list[dict]:
    manifest = manifest or path.parent / "run_manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("outputs", {}).get("evidence", {}).get("sha256") != sha256_file(path):
        raise ValueError("Reconciled evidence does not match its manifest hash")
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records or any("subject" not in r or "evidence" not in r for r in records):
        raise ValueError("Expected nonempty reconciled evidence JSONL")
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="VariantGate evidence-to-brief workflow"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--reconciled", type=Path, help="Existing reconciled JSONL; no retrieval calls"
    )
    source.add_argument(
        "--input",
        type=Path,
        help="Prepared INT8 variants; run CPU triage and NCBI retrieval",
    )
    parser.add_argument("--evidence-manifest", type=Path)
    parser.add_argument(
        "--manifest", type=Path, help="Two-layer scoring export, e.g. frozen V2"
    )
    parser.add_argument("--routing-policy", type=Path)
    parser.add_argument(
        "--assembly", choices=["GRCh38"], help="Explicit input coordinate assembly"
    )
    parser.add_argument("--split")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--route", choices=["deep_review", "light_review"], default="deep_review"
    )
    parser.add_argument("--variation-id-column")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--llm-backend", choices=["extractive", "openai", "local"], default="extractive"
    )
    parser.add_argument(
        "--llm-model", help="Explicit provider model ID; no implicit model choice"
    )
    parser.add_argument("--llm-base-url")
    parser.add_argument("--llm-api-key-environment", default="OPENAI_API_KEY")
    parser.add_argument("--llm-timeout", type=float, default=90)
    parser.add_argument("--llm-retries", type=int, default=0)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument(
        "--max-calls",
        type=int,
        default=100,
        help="Maximum new generation calls this invocation (HTTP retries separate)",
    )
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--max-articles", type=int, default=12)
    parser.add_argument("--max-passage-chars", type=int, default=16000)
    parser.add_argument("--max-queries-per-variant", type=int, default=4)
    parser.add_argument("--max-results-per-query", type=int, default=20)
    parser.add_argument("--cache-dir", type=Path, default=Path("cache/variantgate"))
    parser.add_argument("--email")
    parser.add_argument("--ncbi-api-key-environment", default="NCBI_API_KEY")
    return parser


def run_pipeline(args: argparse.Namespace) -> dict:
    if args.limit <= 0 or args.max_articles <= 0 or args.max_passage_chars <= 0:
        raise ValueError("Record/source limits must be positive")
    if args.input and (args.manifest is None or args.routing_policy is None):
        raise ValueError("--input requires --manifest and --routing-policy")
    backend = (
        ExtractiveBackend()
        if args.llm_backend == "extractive"
        else JsonAPIBackend(
            args.llm_backend,
            args.llm_model or "",
            args.llm_base_url,
            args.llm_api_key_environment,
            args.llm_timeout,
            args.llm_retries,
            args.max_output_tokens,
        )
    )
    root = args.output_dir.resolve()
    paths = {
        k: getattr(args, k)
        for k in (
            "input",
            "reconciled",
            "evidence_manifest",
            "manifest",
            "routing_policy",
        )
    }
    if args.reconciled and args.evidence_manifest is None:
        paths["evidence_manifest"] = args.reconciled.parent / "run_manifest.json"
    config = {
        "pipeline_version": BRIEF_PIPELINE_VERSION,
        "inputs": {
            k: {"path": str(p.resolve()), "sha256": sha256_file(p)}
            for k, p in paths.items()
            if p is not None
        },
        "backend": backend.identity,
        "options": {
            k: str(getattr(args, k))
            if isinstance(getattr(args, k), Path)
            else getattr(args, k)
            for k in (
                "assembly",
                "split",
                "limit",
                "route",
                "variation_id_column",
                "max_articles",
                "max_passage_chars",
                "max_queries_per_variant",
                "max_results_per_query",
                "cache_dir",
                "email",
            )
        },
        "prompts_sha256": digest([EXTRACTION_PROMPT, EXTRACTION_SCHEMA]),
        "code_sha256": {
            str(p.relative_to(Path(__file__).parent)): sha256_file(p)
            for p in sorted(Path(__file__).parent.rglob("*.py"))
            if "tests" not in p.parts
        },
    }
    state_path = root / "pipeline_state.json"
    if state_path.exists():
        if not args.resume:
            raise ValueError("Output run exists; use --resume or a new directory")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["configuration"] != config:
            raise ValueError(
                "Resume configuration/input/code mismatch; use a new directory"
            )
    else:
        if args.resume:
            raise ValueError("No saved pipeline state to resume")
        if root.exists() and any(root.iterdir()):
            raise ValueError("Output directory is not empty")
        root.mkdir(parents=True, exist_ok=True)
        state = {"format_version": 1, "configuration": config, "stages": {}}
        write_json(state_path, state)

    def stage(name: str, module, options: list[str], filename: str) -> Path:
        existing = state["stages"].get(name)
        if existing:
            for entry in existing["outputs"].values():
                path = Path(entry["path"])
                if not path.exists() or sha256_file(path) != entry["sha256"]:
                    raise ValueError(f"Completed {name} stage was modified or removed")
            return Path(existing["result"])
        directory = root / "stages" / (name + "-" + uuid.uuid4().hex[:12])
        arguments = module.build_parser().parse_args(
            options + ["--output-dir", str(directory)]
        )
        function = getattr(arguments, "function", None)
        if function is None:
            function = module.run_reconcile
        function(arguments)
        result = directory / filename
        outputs = {
            p.name: {"path": str(p), "sha256": sha256_file(p)}
            for p in directory.iterdir()
            if p.is_file()
        }
        state["stages"][name] = {"result": str(result), "outputs": outputs}
        write_json(state_path, state)
        return result

    try:
        if args.reconciled:
            records = read_evidence(args.reconciled, paths["evidence_manifest"])[
                : args.limit
            ]
        else:
            from software.variantgate import (
                cli,
                direct_pubmed_cli,
                evidence_cli,
                pubmed_cli,
                reconcile_cli,
            )

            triage = [
                "triage",
                "--input",
                str(args.input),
                "--manifest",
                str(args.manifest),
                "--routing-policy",
                str(args.routing_policy),
                "--backend",
                "numpy",
            ]
            # Limit after routing, not before; a small input prefix can contain no deep-review rows.
            if args.split:
                triage += ["--split", args.split]
            scores = stage("triage", cli, triage, "scores.parquet")
            network = ["--api-key-environment", args.ncbi_api_key_environment]
            if args.email:
                network += ["--email", args.email]
            collect = [
                "collect-clinvar",
                "--scores",
                str(scores),
                "--variants",
                str(args.input),
                "--routing-policy",
                str(args.routing_policy),
                "--route",
                args.route,
                "--limit",
                str(args.limit),
                "--cache-dir",
                str(args.cache_dir / "clinvar"),
            ] + network
            if args.variation_id_column:
                collect += ["--variation-id-column", args.variation_id_column]
            clinvar = stage("clinvar", evidence_cli, collect, "clinvar_evidence.jsonl")
            # Fail before searches when the actual ClinVar source contradicts the input.
            from software.variantgate.grounding import identity_check

            cv_records = [
                json.loads(x) for x in clinvar.read_text(encoding="utf-8").splitlines()
            ]
            for record in cv_records:
                check = identity_check(
                    record["variant"], record["evidence"], args.assembly
                )
                if check["status"] != "verified":
                    raise ValueError(
                        "ClinVar identity verification failed before literature retrieval: "
                        + record["variant"]["variant_key"]
                        + ": "
                        + ", ".join(check["issues"])
                    )
            linked = stage(
                "pubmed",
                pubmed_cli,
                [
                    "collect",
                    "--clinvar-evidence",
                    str(clinvar),
                    "--with-abstracts",
                    "--cache-dir",
                    str(args.cache_dir / "pubmed"),
                ]
                + network,
                "pubmed_evidence.jsonl",
            )
            direct = stage(
                "direct_pubmed",
                direct_pubmed_cli,
                [
                    "collect",
                    "--clinvar-evidence",
                    str(clinvar),
                    "--linked-pubmed-evidence",
                    str(linked),
                    "--cache-dir",
                    str(args.cache_dir / "pubmed"),
                    "--max-queries-per-variant",
                    str(args.max_queries_per_variant),
                    "--max-results-per-query",
                    str(args.max_results_per_query),
                ]
                + network,
                "direct_pubmed_evidence.jsonl",
            )
            reconciled = stage(
                "reconcile",
                reconcile_cli,
                [
                    "--clinvar-evidence",
                    str(clinvar),
                    "--pubmed-evidence",
                    str(linked),
                    "--direct-pubmed-evidence",
                    str(direct),
                ],
                "reconciled_evidence.jsonl",
            )
            records = read_evidence(reconciled)
        summary = run_briefs(
            records,
            root / "briefs",
            backend,
            args.assembly,
            args.max_articles,
            args.max_passage_chars,
            args.max_calls,
            args.input_usd_per_million,
            args.output_usd_per_million,
        )
        state["status"] = summary["status"]
        state.pop("failure", None)
        write_json(state_path, state)
        files = [
            root / "briefs" / name
            for name in (
                "evidence_packages.jsonl",
                "variant_briefs.jsonl",
                "failures.jsonl",
                "report.md",
                "summary.json",
                "run_manifest.json",
            )
        ]
        write_json(
            root / "run_manifest.json",
            {
                "format_version": 1,
                "tool": "variantgate_pipeline",
                "configuration": config,
                "state_sha256": sha256_file(state_path),
                "outputs": {
                    p.name: {"path": str(p), "sha256": sha256_file(p)} for p in files
                },
            },
        )
        return summary
    except Exception as error:
        state["status"] = "failed"
        state["failure"] = {
            "error_type": type(error).__name__,
            "message": str(error)
            if isinstance(error, ValueError)
            else "Upstream stage failed",
        }
        write_json(state_path, state)
        raise


def main() -> None:
    args = build_parser().parse_args()
    try:
        summary = run_pipeline(args)
        print(json.dumps(summary, indent=2))
        raise SystemExit(0 if summary["status"] == "pass" else 2)
    except (ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
