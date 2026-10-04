"""Build bounded, source-addressable evidence; never infer identity from a score."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from software.variantgate.evidence.direct_pubmed import extract_identifiers

PACKAGE_VERSION = 1
GRCH38_VERSIONS = [
    11,
    12,
    12,
    12,
    10,
    12,
    14,
    11,
    12,
    11,
    10,
    12,
    11,
    9,
    10,
    10,
    11,
    10,
    10,
    11,
    9,
    11,
    11,
    10,
]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def literal(text: str, term: str) -> bool:
    return bool(
        term
        and re.search(
            r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, flags=re.IGNORECASE
        )
    )


def exact_terms(text: str, identifiers: dict, gene: str) -> list[str]:
    matches = [
        v
        for v in identifiers["rsids"] + identifiers["canonical_spdi"]
        if literal(text, v)
    ]
    # Short c./p. expressions and protein changes are isoform-dependent.
    # Require a source transcript accession in the same quoted passage.
    transcripts = identifiers.get("transcript_accessions") or []
    if literal(text, gene) and any(literal(text, t) for t in transcripts):
        matches.extend(
            v
            for v in identifiers["hgvs"] + identifiers["protein_changes"]
            if literal(text, v)
        )
    return sorted(set(matches))


def identity_check(subject: dict, clinvar: dict, assembly: str | None) -> dict:
    issues: list[str] = []
    gene = str(subject.get("gene") or "")
    genes = [str(g.get("symbol")) for g in clinvar.get("genes", []) if g.get("symbol")]
    if gene and genes and gene.casefold() not in {g.casefold() for g in genes}:
        issues.append("clinvar_gene_mismatch")
    if gene and not genes:
        issues.append("clinvar_gene_unverified")
    if clinvar.get("variation_id") != subject.get("clinvar_variation_id"):
        issues.append("clinvar_variation_id_mismatch")
    source_id = clinvar.get("source_variation_id")
    accession = re.fullmatch(r"VCV(\d+)(?:\.\d+)?", str(clinvar.get("accession") or ""))
    if source_id is None and accession:
        source_id = int(accession[1])
    if source_id is not None and source_id != subject.get("clinvar_variation_id"):
        issues.append("clinvar_source_uid_mismatch")
    if clinvar.get("status") != "found":
        issues.append("clinvar_not_found")
    key = str(subject.get("variant_key", ""))
    matched = False
    method = None
    # A database-ID input is checked against the returned source ID, not a copied field.
    db_key = re.fullmatch(r"(?:VCV)?(\d+)(?:\.\d+)?", key, re.IGNORECASE)
    if db_key and int(db_key[1]) == source_id:
        matched, method = True, "variation_id_key"
    coordinate = re.fullmatch(
        r"(?:chr)?(\d+|X|Y|M|MT):(\d+):([ACGT]+):([ACGT]+)", key, re.IGNORECASE
    )
    if coordinate and assembly == "GRCh38":
        chrom, pos, ref, alt = coordinate.groups()
        chrom = chrom.upper()
        number = {"X": 23, "Y": 24}.get(chrom)
        if number is None and chrom.isdigit():
            number = int(chrom)
        if number and 1 <= number <= 24:
            accession = f"NC_{number:06d}.{GRCH38_VERSIONS[number - 1]}"
        elif chrom in {"M", "MT"}:
            accession = "NC_012920.1"
        else:
            accession = ""
        # Only SNVs: VCF/SPDI indel normalization needs a separate implementation.
        if len(ref) == len(alt) == 1 and accession:
            expected = f"{accession}:{int(pos) - 1}:{ref.upper()}:{alt.upper()}"
            spdis = clinvar.get("canonical_spdi") or []
            matched = expected in spdis
            method = "grch38_snv_spdi" if matched else None
            if spdis and not matched:
                issues.append("clinvar_coordinate_mismatch")
    if not matched and not issues:
        issues.append("variant_identity_unverified")
    return {
        "status": "verified" if matched and not issues else "blocked",
        "method": method,
        "issues": issues,
        "source_genes": genes,
        "assembly": assembly,
        "requires_human_review": True,
    }


def build_package(
    record: dict,
    assembly: str | None = None,
    max_articles: int = 12,
    max_passage_chars: int = 16000,
) -> dict:
    if max_articles <= 0 or max_passage_chars <= 0:
        raise ValueError("Evidence limits must be positive")
    subject = dict(record["subject"])
    clinvar = record["evidence"]["clinvar"]
    identity = identity_check(subject, clinvar, assembly)
    identifiers = extract_identifiers({"variant": subject, "evidence": clinvar})
    identifiers["genome_assembly"] = assembly
    identifiers["assembly_known"] = assembly is not None
    identifiers["transcript_accessions"] = sorted(
        set(re.findall(r"\bN[MR]_\d+\.\d+\b", str(clinvar.get("title") or "")))
    )

    # Content-addressed passages are stable across variants and corpus ordering.
    def passage(kind: str, text: str, url: str, provenance: dict) -> dict:
        text = text[:max_passage_chars]
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        return {
            "source_id": f"{kind}:{content_hash[:20]}",
            "kind": kind,
            "text": text,
            "text_sha256": content_hash,
            "url": url,
            "provenance": provenance,
        }

    sources: list[dict] = []
    if clinvar.get("status") == "found":
        text = json.dumps(
            {
                k: clinvar.get(k)
                for k in (
                    "variation_id",
                    "title",
                    "genes",
                    "germline_classification",
                    "canonical_spdi",
                )
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        cv = passage(
            "clinvar",
            text,
            clinvar.get("clinvar_url") or "",
            record.get("provenance", {}).get("clinvar", {}),
        )
        cv["classification"] = (clinvar.get("germline_classification") or {}).get(
            "description"
        )
        sources.append(cv)
    merged: dict[str, dict] = {}
    abstract_conflicts: set[str] = set()
    for provider in ("pubmed", "direct_pubmed"):
        for article in (record["evidence"].get(provider) or {}).get("articles", []):
            pmid = str(article.get("pmid") or "")
            if not pmid.isdigit():
                continue
            current = merged.setdefault(pmid, {"pmid": pmid, "memberships": []})
            if (
                current.get("abstract")
                and article.get("abstract")
                and current["abstract"] != article["abstract"]
            ):
                abstract_conflicts.add(pmid)
            for field, value in article.items():
                if value is not None and value != "" and value != []:
                    current[field] = value
            current["memberships"].append(provider)
    ranked = sorted(
        merged.values(),
        key=lambda a: (
            -int((a.get("retrieval_relevance") or {}).get("score", 0)),
            int(a["pmid"]),
        ),
    )
    missing = []
    for article in ranked[:max_articles]:
        if article["pmid"] in abstract_conflicts:
            continue
        abstract = str(article.get("abstract") or "").strip()
        if not abstract:
            missing.append(article["pmid"])
            continue  # Titles and ClinVar links do not prove a paper's findings.
        source = passage(
            "pubmed:" + article["pmid"],
            abstract,
            f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/",
            {
                k: article.get(k)
                for k in (
                    "abstract_provenance",
                    "summary_provenance",
                    "retrieved_at",
                    "raw_summary_sha256",
                )
            },
        )
        text = source["text"]
        gene_match = literal(text, str(subject.get("gene") or ""))
        exact = exact_terms(text, identifiers, str(subject.get("gene") or ""))
        source.update(
            pmid=article["pmid"],
            title=article.get("title"),
            memberships=sorted(set(article["memberships"])),
            retrieval_relevance=article.get("retrieval_relevance"),
            matched_identifiers=sorted(set(exact)),
            identity_scope="exact_variant"
            if exact
            else "gene_context"
            if gene_match
            else "unresolved",
            truncated=len(abstract) > max_passage_chars,
        )
        sources.append(source)
    package = {
        "format_version": PACKAGE_VERSION,
        "subject": subject,
        "evidence_origin": "synthetic_fixture"
        if record.get("provenance", {}).get("clinvar", {}).get("origin")
        == "synthetic_fixture"
        else "supplied_evidence",
        "identity": identity,
        "identifiers": identifiers,
        "triage": record.get("fpga_gate", {}),
        "sources": sources,
        "gaps": {
            "missing_abstract_pmids": missing,
            "conflicting_abstract_pmids": sorted(abstract_conflicts),
            "omitted_article_number": max(0, len(ranked) - max_articles),
            "literature_passage_number": sum(
                s["kind"].startswith("pubmed:") for s in sources
            ),
        },
        "input_record_sha256": digest(record),
        "boundary": "Research evidence brief; scoring is not independent evidence. "
        "No clinical classification or ACMG evidence-strength assignment.",
    }
    package["package_sha256"] = digest(package)
    return package


def verify_package(package: dict) -> None:
    unhashed = {k: v for k, v in package.items() if k != "package_sha256"}
    if digest(unhashed) != package.get("package_sha256"):
        raise ValueError("Evidence package hash mismatch")
    ids = [s["source_id"] for s in package["sources"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source IDs")
