from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from software.variantgate.manifest import sha256_file
from software.variantgate.reports import aggregate_reports, build_variant_report


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        for value in values:
            output_file.write(json.dumps(value, sort_keys=True) + "\n")


def load_jsonl(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue

            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}"
                ) from error

            if not isinstance(value, dict):
                raise ValueError(
                    f"Line {line_number} of {path} is not a JSON object"
                )

            records.append(value)

    if not records:
        raise ValueError(f"Evidence file contains no records: {path}")

    return records


def validate_evidence_manifest(
    evidence_path: Path,
    evidence_manifest_path: Path,
) -> dict[str, object]:
    manifest = json.loads(
        evidence_manifest_path.read_text(encoding="utf-8")
    )

    if not isinstance(manifest, dict):
        raise ValueError("Evidence run manifest is not a JSON object")

    recorded_evidence = manifest.get("outputs", {}).get("evidence", {})

    if recorded_evidence.get("sha256") != sha256_file(evidence_path):
        raise ValueError(
            "Evidence file does not match its run-manifest hash"
        )

    return manifest


def markdown_table(
    headers: list[str],
    rows: list[list[object]],
) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]

    for row in rows:
        escaped = [
            str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
            for value in row
        ]
        lines.append("| " + " | ".join(escaped) + " |")

    return lines


def build_markdown(
    reports: list[dict[str, object]],
    summary: dict[str, object],
    preview_rows: int,
) -> str:
    lines = [
        "# VariantGate Evidence Report",
        "",
        "Research triage output only. This report is not a clinical diagnosis.",
        "",
        "## Aggregate summary",
        "",
        f"- Variants: {summary['variant_number']:,}",
        f"- Unique genes: {summary['unique_gene_number']:,}",
        (
            "- ClinVar evidence status: "
            + json.dumps(summary["evidence_status_counts"], sort_keys=True)
        ),
        (
            "- ClinVar classification groups: "
            + json.dumps(
                summary["classification_group_counts"],
                sort_keys=True,
            )
        ),
        (
            "- Direction-only comparisons: "
            + json.dumps(summary["comparison_counts"], sort_keys=True)
        ),
        "",
        "## Variant preview",
        "",
    ]
    preview = reports[:preview_rows]
    rows: list[list[object]] = []

    for report in preview:
        rows.append(
            [
                report["subject"]["variant_key"],
                report["subject"].get("gene") or "",
                report["fpga_gate"]["score"],
                report["fpga_gate"]["route"],
                report["clinvar_statement"].get("classification") or "",
                report["clinvar_statement"].get("review_status") or "",
                report["comparison"]["category"],
            ]
        )

    lines.extend(
        markdown_table(
            [
                "Variant",
                "Gene",
                "Score",
                "Route",
                "ClinVar",
                "Review status",
                "Comparison",
            ],
            rows,
        )
    )
    lines.extend(
        [
            "",
            (
                f"Preview shows {len(preview):,} of "
                f"{len(reports):,} variants. Complete structured reports are "
                "stored in `variant_evidence_reports.jsonl`."
            ),
            "",
            "## Interpretation boundary",
            "",
            (
                "The FPGA output is a deterministic prioritization signal. "
                "The comparison category only checks binary direction against "
                "the retrieved ClinVar classification and is not an independent "
                "accuracy estimate."
            ),
            "",
            (
                "The current model was trained from ClinVar-derived labels. "
                "Current ClinVar evidence for the same variants is therefore "
                "circular and should be used to verify retrieval, provenance, "
                "and reporting—not predictive validity."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def run_build(args: argparse.Namespace) -> None:
    evidence_path = args.evidence.resolve()
    evidence_manifest_path = (
        args.evidence_manifest.resolve()
        if args.evidence_manifest is not None
        else evidence_path.parent / "run_manifest.json"
    )
    evidence_manifest = validate_evidence_manifest(
        evidence_path=evidence_path,
        evidence_manifest_path=evidence_manifest_path,
    )
    evidence_records = load_jsonl(evidence_path)
    reports = [build_variant_report(record) for record in evidence_records]
    variant_keys = [report["subject"]["variant_key"] for report in reports]

    if len(variant_keys) != len(set(variant_keys)):
        raise ValueError("Evidence contains duplicate variant keys")

    aggregate = aggregate_reports(reports)
    summary = {
        "status": "pass",
        **aggregate.to_dict(),
        "interpretation_boundary": (
            "Direction-only audit comparison; not independent model "
            "validation and not a clinical diagnosis."
        ),
    }
    output_directory = args.output_dir.resolve()
    output_directory.mkdir(parents=True, exist_ok=False)
    reports_path = output_directory / "variant_evidence_reports.jsonl"
    summary_path = output_directory / "report_summary.json"
    markdown_path = output_directory / "report.md"
    write_jsonl(reports_path, reports)
    write_json(summary_path, summary)
    markdown_path.write_text(
        build_markdown(
            reports=reports,
            summary=summary,
            preview_rows=args.preview_rows,
        ),
        encoding="utf-8",
        newline="\n",
    )
    report_manifest = {
        "format_version": 1,
        "tool": "variantgate_report",
        "created_at": utc_now(),
        "input": {
            "evidence": {
                "path": str(evidence_path),
                "sha256": sha256_file(evidence_path),
            },
            "evidence_manifest": {
                "path": str(evidence_manifest_path),
                "sha256": sha256_file(evidence_manifest_path),
                "provider": evidence_manifest.get("provider", {}).get("name"),
            },
        },
        "outputs": {
            "variant_reports": {
                "path": reports_path.name,
                "sha256": sha256_file(reports_path),
            },
            "summary": {
                "path": summary_path.name,
                "sha256": sha256_file(summary_path),
            },
            "markdown": {
                "path": markdown_path.name,
                "sha256": sha256_file(markdown_path),
            },
        },
    }
    manifest_path = output_directory / "report_manifest.json"
    write_json(manifest_path, report_manifest)
    print(json.dumps({**summary, "output_directory": str(output_directory)}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="variantgate-report",
        description="Build deterministic reports from VariantGate evidence",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser(
        "build",
        help="Build structured and Markdown evidence reports",
    )
    build_parser.add_argument("--evidence", type=Path, required=True)
    build_parser.add_argument("--evidence-manifest", type=Path, default=None)
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--preview-rows", type=int, default=25)
    build_parser.set_defaults(function=run_build)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.preview_rows <= 0:
        raise ValueError("--preview-rows must be positive")

    args.function(args)


if __name__ == "__main__":
    main()
