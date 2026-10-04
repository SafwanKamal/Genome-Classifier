"""Evaluate a frozen extraction corpus; explicitly separate plumbing from semantics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from software.variantgate.evidence.pubmed import write_json
from software.variantgate.manifest import sha256_file


def evaluate(
    cases_path: Path,
    briefs_path: Path,
    summary_path: Path,
    corpus_manifest: Path | None = None,
) -> dict:
    manifest_path = corpus_manifest or cases_path.parent / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["outputs"]["cases"]["sha256"] != sha256_file(cases_path):
        raise ValueError("Frozen evaluation corpus hash mismatch")
    brief_manifest = json.loads(
        (briefs_path.parent / "run_manifest.json").read_text(encoding="utf-8")
    )
    for path in (briefs_path, summary_path):
        if brief_manifest["outputs"][path.name]["sha256"] != sha256_file(path):
            raise ValueError("Evaluated output does not match its run-manifest hash")
    cases = [
        json.loads(x) for x in cases_path.read_text(encoding="utf-8").splitlines() if x
    ]
    briefs = [
        json.loads(x) for x in briefs_path.read_text(encoding="utf-8").splitlines() if x
    ]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    keys = [b["subject"]["variant_key"] for b in briefs]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate evaluated variants")
    indexed = dict(zip(keys, briefs))
    expected_keys = {c["record"]["subject"]["variant_key"] for c in cases}
    if set(indexed) - expected_keys:
        raise ValueError("Unexpected variants in evaluated briefs")
    details, matched, total = [], 0, 0
    for case in cases:
        key = case["record"]["subject"]["variant_key"]
        brief = indexed.get(key)
        reference = case["reference"]
        expected = reference["identity_status"]
        identity_ok = brief is not None and brief["identity"]["status"] == expected
        claims = brief["claims"] if brief else []
        literature = [c for c in claims if c["finding_type"] != "database_statement"]
        reference_findings = reference["findings"] if expected == "verified" else []
        matched_case = sum(
            any(
                ref["quote"] in claim["quote"]
                and ref["scope"] == claim["scope"]
                and ref["finding_type"] == claim["finding_type"]
                for claim in literature
            )
            for ref in reference_findings
        )
        matched += matched_case
        total += len(reference_findings)
        if expected == "blocked":
            scope_ok = not claims
        else:
            allowed = {r["scope"] for r in reference_findings}
            scope_ok = all(c["scope"] in allowed for c in literature)
        details.append(
            {
                "case_id": case["case_id"],
                "identity_check_passed": identity_ok,
                "scope_check_passed": scope_ok,
                "brief_present": brief is not None,
                "reference_findings_covered": matched_case,
                "reference_finding_number": len(reference_findings),
            }
        )
    return {
        "status": "pass"
        if all(d["identity_check_passed"] and d["scope_check_passed"] for d in details)
        and not summary["failed_variant_number"]
        else "fail",
        "case_number": len(cases),
        "backend": summary["backend"],
        "offline_control": summary["offline_control"],
        "identity_checks_passed": sum(d["identity_check_passed"] for d in details),
        "scope_checks_passed": sum(d["scope_check_passed"] for d in details),
        "reference_findings_covered": matched,
        "reference_finding_number": total,
        "reference_finding_coverage": matched / total if total else None,
        "semantic_support_accuracy": None,
        "reviewer_usefulness": None,
        "interpretation": "Status tests identity and scope guards only. Quote/type coverage is a proxy; "
        "expert adjudication of semantic support and usefulness remains required. "
        "Synthetic fixtures cannot establish real-world LLM or clinical quality.",
        "inputs": {
            "cases_sha256": sha256_file(cases_path),
            "briefs_sha256": sha256_file(briefs_path),
        },
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen VariantGate extraction cases"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--briefs", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.cases, args.briefs, args.summary, args.corpus_manifest)
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 2)


if __name__ == "__main__":
    main()
