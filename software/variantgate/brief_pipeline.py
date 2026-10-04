"""Mechanical grounding checks and resumable evidence-to-brief execution."""

from __future__ import annotations

import json
import time
from pathlib import Path

from software.variantgate.evidence.pubmed import write_json
from software.variantgate.grounding import (
    build_package,
    digest,
    exact_terms,
    literal,
    verify_package,
)
from software.variantgate.llm import (
    EXTRACTION_PROMPT,
    EXTRACTION_SCHEMA,
    PROMPT_VERSION,
    SECTIONS,
    SYNTHESIS_SCHEMA,
    Backend,
    section_for,
)
from software.variantgate.manifest import sha256_file

BRIEF_PIPELINE_VERSION = "deterministic-database-and-grouping-v2"


def check_schema(value: object, schema: dict, location: str = "output") -> None:
    """Validate the deliberately small JSON Schema subset used by this pipeline."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ValueError(f"{location}: object fields differ from schema")
        for key, child in schema["properties"].items():
            check_schema(value[key], child, location + "." + key)
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError(f"{location}: expected array")
        for child in value:
            check_schema(child, schema["items"], location + "[]")
    elif kind == "string":
        if not isinstance(value, str) or (
            "enum" in schema and value not in schema["enum"]
        ):
            raise ValueError(f"{location}: invalid string")
    else:
        raise ValueError("Unsupported internal schema type")


def validate_extraction(package: dict, source: dict, output: dict) -> dict:
    verify_package(package)
    if package["identity"]["status"] != "verified":
        raise ValueError("Cannot extract claims for unverified variant identity")
    check_schema(output, EXTRACTION_SCHEMA)
    if output["source_id"] != source["source_id"]:
        raise ValueError("Extraction cites the wrong source")
    if source not in package["sources"]:
        raise ValueError("Source is not in the frozen package")
    if len(output["claims"]) > 8:
        raise ValueError("Too many claims for one source (maximum eight)")
    accepted = []
    for claim in output["claims"]:
        quote = claim["quote"]
        if len(quote.strip()) < 24 or quote not in source["text"]:
            raise ValueError("Supporting quote is missing, too short, or not verbatim")
        if not claim["statement"].strip() or len(claim["statement"]) > 16000:
            raise ValueError("Invalid claim statement")
        is_database = source["kind"] == "clinvar"
        if (claim["finding_type"] == "database_statement") != is_database:
            raise ValueError(
                "Database statements and literature findings must remain separate"
            )
        gene = str(package["subject"].get("gene") or "")
        ids = package["identifiers"]
        exact = bool(exact_terms(quote, ids, gene))
        if claim["scope"] == "exact_variant" and not is_database and not exact:
            raise ValueError("Quote does not identify the exact variant")
        if claim["scope"] == "gene_context" and not literal(quote, gene):
            raise ValueError("Gene-context quote does not name the input gene")
        accepted.append(
            {
                **claim,
                "source_id": source["source_id"],
                "quote_start": source["text"].index(quote),
                "quote_end": source["text"].index(quote) + len(quote),
                "claim_id": "claim:" + digest([source["source_id"], claim])[:24],
                "validation": "mechanical_checks_passed",
                "semantic_support": "requires_human_review",
            }
        )
    if len({c["claim_id"] for c in accepted}) != len(accepted):
        raise ValueError("Duplicate extracted claims")
    return {
        "source_id": source["source_id"],
        "claims": accepted,
        "limitations": output["limitations"],
        "requires_human_review": True,
    }


def validate_synthesis(claims: list[dict], output: dict) -> dict:
    check_schema(output, SYNTHESIS_SCHEMA)
    indexed = {c["claim_id"]: c for c in claims}
    selected = [key for s in SECTIONS for key in output[s]]
    if len(selected) != len(set(selected)) or set(selected) != set(indexed):
        raise ValueError("Synthesis must retain every accepted claim exactly once")
    for section in SECTIONS:
        if any(section_for(indexed[key]) != section for key in output[section]):
            raise ValueError("Synthesis changes a claim's evidence scope")
    return output


def database_extraction(source: dict) -> dict:
    """Render verified source fields without model inference or reclassification."""
    try:
        fields = json.loads(source["text"])
    except json.JSONDecodeError:
        raise ValueError(
            "ClinVar structured passage is incomplete; increase --max-passage-chars"
        ) from None
    classification = fields.get("germline_classification") or {}
    description = classification.get("description")
    status = classification.get("review_status")
    statement = f"ClinVar record {fields['variation_id']}"
    if fields.get("title"):
        statement += f": {fields['title']}"
    if description:
        statement += f". Reported germline classification: {description}"
    if status:
        statement += f". Reported review status: {status}"
    return {
        "source_id": source["source_id"],
        "claims": [
            {
                "statement": statement + ".",
                "quote": source["text"],
                "scope": "exact_variant",
                "finding_type": "database_statement",
            }
        ],
        "limitations": [],
    }


def group_claims(claims: list[dict]) -> dict:
    sections = {s: [] for s in SECTIONS}
    for claim in claims:
        sections[section_for(claim)].append(claim["claim_id"])
    return validate_synthesis(claims, sections)


class CallCache:
    def __init__(
        self,
        root: Path,
        backend: Backend,
        max_calls: int = 100,
        input_price: float | None = None,
        output_price: float | None = None,
    ):
        if max_calls < 0 or any(
            p is not None and p < 0 for p in (input_price, output_price)
        ):
            raise ValueError("Invalid budget/pricing")
        self.root, self.backend, self.max_calls = root, backend, max_calls
        self.input_price, self.output_price = input_price, output_price
        self.new_calls = 0
        self.receipts: dict[str, dict] = {}

    def call(self, stage: str, payload: dict, schema: dict, prompt: str) -> dict:
        key = digest(
            {
                "stage": stage,
                "payload": payload,
                "schema": schema,
                "prompt": prompt,
                "backend": self.backend.identity,
            }
        )
        path = self.root / (key + ".json")
        if path.exists():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            receipt = envelope["receipt"]
            if envelope.get("key") != key or envelope.get("sha256") != digest(receipt):
                raise ValueError("LLM response cache hash mismatch")
        else:
            if self.new_calls >= self.max_calls:
                raise ValueError(
                    "LLM call budget exhausted; resume with a higher --max-calls"
                )
            self.new_calls += 1
            receipt = self.backend.generate(stage, payload, schema, prompt)
            # Persist before validation so rejected responses retain usage/provenance.
            write_json(
                path, {"key": key, "sha256": digest(receipt), "receipt": receipt}
            )
        self.receipts[key] = receipt
        return receipt["data"]

    def accounting(self) -> dict:
        usages = [r.get("usage") or {} for r in self.receipts.values()]
        input_tokens = sum(
            u.get("input_tokens", u.get("prompt_tokens", 0)) or 0 for u in usages
        )
        output_tokens = sum(
            u.get("output_tokens", u.get("completion_tokens", 0)) or 0 for u in usages
        )
        known = bool(usages) and all(bool(u) for u in usages)
        cost = (
            (input_tokens * self.input_price + output_tokens * self.output_price) / 1e6
            if known and self.input_price is not None and self.output_price is not None
            else None
        )
        return {
            "new_generation_calls": self.new_calls,
            "unique_responses": len(self.receipts),
            "provider_latency_seconds_for_used_responses": sum(
                r.get("latency_seconds", 0.0) for r in self.receipts.values()
            ),
            "input_tokens": input_tokens if known else None,
            "output_tokens": output_tokens if known else None,
            "estimated_cost_usd": cost,
            "pricing_source": "user_supplied" if cost is not None else None,
            "usage_scope": "Responses used by this run, including reused cached responses; "
            "HTTP retries and uncertain failed deliveries may incur additional cost.",
        }


def build_brief(package: dict, calls: CallCache) -> dict:
    verify_package(package)
    result = {
        "format_version": 1,
        "subject": package["subject"],
        "evidence_origin": package["evidence_origin"],
        "package_sha256": package["package_sha256"],
        "identity": package["identity"],
        "triage": package["triage"],
        "sources": package["sources"],
        "gaps": package["gaps"],
        "extractions": [],
        "claims": [],
        "sections": {s: [] for s in SECTIONS},
        "requires_human_review": True,
        "boundary": package["boundary"],
        "backend": calls.backend.identity,
        "prompt_version": PROMPT_VERSION,
        "pipeline_version": BRIEF_PIPELINE_VERSION,
        "organization_method": "deterministic_validated_scope",
    }
    if package["identity"]["status"] != "verified":
        result["status"] = "blocked_identity"
        return result
    for source in package["sources"]:
        if source["kind"] == "clinvar":
            raw = database_extraction(source)
            method = "structured_database_fields"
        else:
            payload = {
                "subject": package["subject"],
                "identifiers": package["identifiers"],
                "source": source,
            }
            raw = calls.call("extract", payload, EXTRACTION_SCHEMA, EXTRACTION_PROMPT)
            method = (
                "offline_verbatim_control"
                if calls.backend.identity["provider"] == "extractive"
                else "model_literature_extraction"
            )
        extraction = validate_extraction(package, source, raw)
        extraction["generation_method"] = method
        for claim in extraction["claims"]:
            claim["generation_method"] = method
        result["extractions"].append(extraction)
        result["claims"].extend(extraction["claims"])
    result["sections"] = group_claims(result["claims"])
    result["status"] = "review_required"
    return result


def markdown(briefs: list[dict]) -> str:
    import html

    def safe(value: object) -> str:
        # Source/model strings cannot inject Markdown links, HTML or headings.
        text = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
        for char in "\\`*_{}[]()#+.!|":
            text = text.replace(char, "\\" + char)
        return text

    lines = [
        "# VariantGate Research Evidence Briefs",
        "",
        "Human review required. Mechanical citation checks do not verify semantic support.",
        "",
        (
            "ClinVar statements come directly from database fields. Claims are grouped by code; "
            "the selected backend extracts literature only."
        ),
        "",
    ]
    for brief in briefs:
        lines.extend(
            [
                "## " + safe(brief["subject"]["variant_key"]),
                "",
                "Status: " + safe(brief["status"]),
                "",
                "Gene: " + safe(brief["subject"].get("gene")),
                "",
                "Triage score (prioritization only): "
                + safe(brief["triage"].get("score")),
                "",
            ]
        )
        if brief["backend"]["provider"] == "extractive":
            lines.extend(
                [
                    "Offline verbatim control: this output does not assess LLM extraction quality.",
                    "",
                ]
            )
        if brief["evidence_origin"] == "synthetic_fixture":
            lines.extend(
                [
                    "Invented test evidence. Genes, identifiers and findings are placeholders.",
                    "",
                ]
            )
        if brief["identity"]["status"] != "verified":
            lines.extend(
                [
                    "Evidence synthesis blocked: "
                    + safe(", ".join(brief["identity"]["issues"])),
                    "",
                ]
            )
            continue
        indexed = {c["claim_id"]: c for c in brief["claims"]}
        sources = {s["source_id"]: s for s in brief["sources"]}
        for section in SECTIONS:
            lines.extend(["### " + section.replace("_", " ").title(), ""])
            if not brief["sections"][section]:
                lines.extend(["No extracted findings in this category.", ""])
            for key in brief["sections"][section]:
                claim = indexed[key]
                source = sources[claim["source_id"]]
                # URLs are constructed for PubMed; ClinVar URLs must be trusted-shaped.
                url = source["url"]
                import re

                if brief["evidence_origin"] == "synthetic_fixture" or not re.fullmatch(
                    r"https://(?:pubmed\.ncbi\.nlm\.nih\.gov/\d+/|www\.ncbi\.nlm\.nih\.gov/clinvar/variation/\d+/)",
                    url,
                ):
                    url = ""
                citation = (
                    f"[{safe(claim['source_id'])}]({url})"
                    if url
                    else safe(claim["source_id"])
                )
                lines.extend(
                    [
                        "- " + safe(claim["statement"]) + " " + citation,
                        "",
                        "> " + safe(claim["quote"]),
                        "",
                        "Generation method: " + safe(claim["generation_method"]),
                        "",
                    ]
                )
        lines.extend(
            [
                "Missing abstracts: " + safe(brief["gaps"]["missing_abstract_pmids"]),
                "",
                "Conflicting abstracts excluded: "
                + safe(brief["gaps"]["conflicting_abstract_pmids"]),
                "",
                "Articles omitted by limit: "
                + str(brief["gaps"]["omitted_article_number"]),
                "",
                "Extraction limitations: "
                + safe(
                    sorted({l for e in brief["extractions"] for l in e["limitations"]})
                ),
                "",
                brief["boundary"],
                "",
            ]
        )
    return "\n".join(lines)


def run_briefs(
    records: list[dict],
    root: Path,
    backend: Backend,
    assembly: str | None = None,
    max_articles: int = 12,
    max_passage_chars: int = 16000,
    max_calls: int = 100,
    input_price: float | None = None,
    output_price: float | None = None,
) -> dict:
    if not records:
        raise ValueError("No reconciled evidence records")
    keys = [r["subject"]["variant_key"] for r in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate variant keys")
    calls = CallCache(
        root / "llm_responses", backend, max_calls, input_price, output_price
    )
    briefs, errors, packages = [], [], []
    start = time.perf_counter()
    for record in records:
        try:
            package = build_package(record, assembly, max_articles, max_passage_chars)
            packages.append(package)
            briefs.append(build_brief(package, calls))
        except Exception as error:  # noqa: BLE001 -- isolate failures and publish no partial brief
            # Provider responses and transport exception details can contain secrets.
            errors.append(
                {
                    "variant_key": record["subject"]["variant_key"],
                    "error_type": type(error).__name__,
                    "message": str(error)
                    if isinstance(error, ValueError)
                    else "Stage failed; no brief accepted. Check backend configuration.",
                }
            )
    root.mkdir(parents=True, exist_ok=True)

    def jsonl(name: str, values: list[dict]) -> None:
        path = root / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            "".join(
                json.dumps(v, sort_keys=True, ensure_ascii=False) + "\n" for v in values
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    jsonl("evidence_packages.jsonl", packages)
    jsonl("variant_briefs.jsonl", briefs)
    jsonl("failures.jsonl", errors)
    report = root / "report.md"
    report.write_text(markdown(briefs), encoding="utf-8")
    summary = {
        "status": "failed"
        if errors
        else "attention_required"
        if any(b["status"] == "blocked_identity" for b in briefs)
        else "pass",
        "requested_variants": len(records),
        "brief_number": len(briefs),
        "blocked_identity_number": sum(
            b["status"] == "blocked_identity" for b in briefs
        ),
        "failed_variant_number": len(errors),
        "elapsed_seconds": time.perf_counter() - start,
        "backend": backend.identity,
        "prompt_version": PROMPT_VERSION,
        "pipeline_version": BRIEF_PIPELINE_VERSION,
        "offline_control": backend.identity["provider"] == "extractive",
        "requires_human_review": True,
        "accounting": calls.accounting(),
    }
    write_json(root / "summary.json", summary)
    files = [
        root / name
        for name in (
            "evidence_packages.jsonl",
            "variant_briefs.jsonl",
            "failures.jsonl",
            "report.md",
            "summary.json",
        )
    ]
    write_json(
        root / "run_manifest.json",
        {
            "format_version": 1,
            "tool": "variantgate_briefs",
            "prompt_version": PROMPT_VERSION,
            "pipeline_version": BRIEF_PIPELINE_VERSION,
            "input_records_sha256": digest(records),
            "backend": backend.identity,
            "outputs": {p.name: {"sha256": sha256_file(p)} for p in files},
        },
    )
    return summary
