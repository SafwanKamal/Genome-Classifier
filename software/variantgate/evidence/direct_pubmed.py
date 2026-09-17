from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable


RSID_PATTERN = re.compile(r"\brs\d+\b", re.IGNORECASE)
VARIANT_KEY_PATTERN = re.compile(
    r"^(?P<chromosome>[^:]+):(?P<position>\d+):"
    r"(?P<reference>[^:]+):(?P<alternate>[^:]+)$"
)
HGVS_PATTERN = re.compile(
    r"(?:[A-Z]{1,8}_\d+(?:\.\d+)?:)?"
    r"[cgmnpr]\."
    r"[A-Za-z0-9_*?+>\-=\[\]()]+",
    re.IGNORECASE,
)
VARIANT_CONTEXT_TERMS = (
    "variant",
    "mutation",
    "missense",
    "substitution",
    "genotype",
    "allele",
)
FUNCTIONAL_TERMS = (
    "functional",
    "assay",
    "activity",
    "expression",
    "splicing",
    "reporter",
    "electrophysiology",
    "transactivation",
    "rescue",
)
CLINICAL_TERMS = (
    "patient",
    "cohort",
    "case",
    "control",
    "family",
    "segregation",
    "phenotype",
)


@dataclass(frozen=True, slots=True)
class SearchPlan:
    strategy: str
    query: str
    identifier: str | None
    specificity: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []

    for value in values:
        if value is None:
            continue

        text = str(value).strip()

        if text and text not in result:
            result.append(text)

    return result


def unpack_strings(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        return unique_strings(value)

    text = str(value).strip()

    if not text:
        return []

    return unique_strings(
        part.strip()
        for part in re.split(r"[,;|]", text)
        if part.strip()
    )


def extract_identifiers(
    clinvar_record: dict[str, object],
) -> dict[str, object]:
    variant = clinvar_record.get("variant")
    evidence = clinvar_record.get("evidence")

    if not isinstance(variant, dict) or not isinstance(evidence, dict):
        raise ValueError("ClinVar record requires variant and evidence objects")

    variant_key = str(variant.get("variant_key", ""))
    key_match = VARIANT_KEY_PATTERN.fullmatch(variant_key)
    coordinates = (
        key_match.groupdict()
        if key_match is not None
        else None
    )
    title = str(evidence.get("title") or "")
    xrefs = evidence.get("database_cross_references") or []
    rsids: list[str] = []

    for xref in xrefs:
        if not isinstance(xref, dict):
            continue

        database = str(xref.get("database", "")).casefold()
        identifier = str(xref.get("identifier", "")).strip()

        if database in {"dbsnp", "snp"}:
            rsids.extend(RSID_PATTERN.findall(identifier))

    rsids.extend(RSID_PATTERN.findall(title))
    hgvs = [
        value.rstrip(".,;:)")
        for value in HGVS_PATTERN.findall(title)
        if value.rstrip(".,;:)")
    ]
    protein_changes = unpack_strings(evidence.get("protein_change"))
    canonical_spdi = unique_strings(evidence.get("canonical_spdi") or [])
    traits = unique_strings(
        trait.get("name")
        for trait in (
            evidence.get("germline_classification", {}).get("traits", [])
            if isinstance(evidence.get("germline_classification"), dict)
            else []
        )
        if isinstance(trait, dict)
    )
    gene = variant.get("gene")
    gene_text = None if gene is None else str(gene).strip() or None
    exact_identifiers = unique_strings(
        [*rsids, *hgvs, *protein_changes, *canonical_spdi]
    )

    return {
        "variant_key": variant_key,
        "gene": gene_text,
        "coordinates": coordinates,
        "genome_assembly": None,
        "rsids": unique_strings(value.casefold() for value in rsids),
        "hgvs": unique_strings(hgvs),
        "protein_changes": protein_changes,
        "canonical_spdi": canonical_spdi,
        "traits": traits,
        "exact_identifiers": exact_identifiers,
        "exact_identifier_number": len(exact_identifiers),
        "assembly_known": False,
        "coordinate_hgvs_generated": False,
    }


def pubmed_term(value: str) -> str:
    safe = value.replace('"', " ").strip()
    return f'"{safe}"[Title/Abstract]'


def build_search_plans(
    identifiers: dict[str, object],
    maximum_query_number: int = 4,
    include_context_query: bool = True,
) -> list[SearchPlan]:
    if maximum_query_number <= 0:
        raise ValueError("maximum_query_number must be positive")

    gene = identifiers.get("gene")
    gene_term = pubmed_term(str(gene)) if gene else None
    candidates: list[SearchPlan] = []

    for strategy, field in (
        ("rsid_exact", "rsids"),
        ("hgvs_exact", "hgvs"),
        ("protein_change_exact", "protein_changes"),
        ("spdi_exact", "canonical_spdi"),
    ):
        for identifier in identifiers.get(field, []) or []:
            identifier_text = str(identifier)
            terms = [pubmed_term(identifier_text)]

            if gene_term and strategy not in {"rsid_exact", "spdi_exact"}:
                terms.append(gene_term)

            candidates.append(
                SearchPlan(
                    strategy=strategy,
                    query=" AND ".join(terms),
                    identifier=identifier_text,
                    specificity="exact_variant_identifier",
                )
            )

    if include_context_query and gene_term:
        traits = identifiers.get("traits", []) or []

        if traits:
            candidates.append(
                SearchPlan(
                    strategy="gene_trait_context",
                    query=(
                        f"{gene_term} AND {pubmed_term(str(traits[0]))} AND "
                        "(variant[Title/Abstract] OR mutation[Title/Abstract])"
                    ),
                    identifier=str(traits[0]),
                    specificity="contextual",
                )
            )
        else:
            candidates.append(
                SearchPlan(
                    strategy="gene_variant_context",
                    query=(
                        f"{gene_term} AND "
                        "(variant[Title/Abstract] OR mutation[Title/Abstract])"
                    ),
                    identifier=None,
                    specificity="contextual",
                )
            )

    deduplicated: list[SearchPlan] = []
    seen_queries: set[str] = set()

    for plan in candidates:
        if plan.query in seen_queries:
            continue

        seen_queries.add(plan.query)
        deduplicated.append(plan)

        if len(deduplicated) == maximum_query_number:
            break

    return deduplicated


def contains_literal(text: str, value: str) -> bool:
    return value.casefold() in text.casefold()


def contains_gene(text: str, gene: str | None) -> bool:
    if not gene:
        return False

    return re.search(
        rf"(?<![A-Za-z0-9]){re.escape(gene)}(?![A-Za-z0-9])",
        text,
        flags=re.IGNORECASE,
    ) is not None


def matched_values(text: str, values: Iterable[object]) -> list[str]:
    return unique_strings(
        str(value)
        for value in values
        if str(value).strip() and contains_literal(text, str(value))
    )


def assess_retrieval_relevance(
    article: dict[str, object],
    identifiers: dict[str, object],
    discovery_strategies: list[str],
) -> dict[str, object]:
    text = "\n".join(
        str(value)
        for value in (
            article.get("title"),
            article.get("abstract"),
            " ".join(str(value) for value in article.get("keywords", []) or []),
            " ".join(str(value) for value in article.get("mesh_terms", []) or []),
        )
        if value
    )
    rsid_matches = matched_values(text, identifiers.get("rsids", []) or [])
    hgvs_matches = matched_values(text, identifiers.get("hgvs", []) or [])
    protein_matches = matched_values(
        text,
        identifiers.get("protein_changes", []) or [],
    )
    spdi_matches = matched_values(
        text,
        identifiers.get("canonical_spdi", []) or [],
    )
    trait_matches = matched_values(text, identifiers.get("traits", []) or [])
    gene_match = contains_gene(text, identifiers.get("gene"))
    lowered = text.casefold()
    variant_context_matches = [
        term for term in VARIANT_CONTEXT_TERMS if term in lowered
    ]
    functional_matches = [
        term for term in FUNCTIONAL_TERMS if term in lowered
    ]
    clinical_matches = [term for term in CLINICAL_TERMS if term in lowered]
    globally_unique_matches = unique_strings([*rsid_matches, *spdi_matches])
    gene_dependent_matches = unique_strings([*hgvs_matches, *protein_matches])
    supported_exact_matches = unique_strings(
        [
            *globally_unique_matches,
            *(gene_dependent_matches if gene_match else []),
        ]
    )
    unsupported_variant_mentions = (
        [] if gene_match else gene_dependent_matches
    )
    score = (
        100 * bool(supported_exact_matches)
        + 10 * len(supported_exact_matches)
        + 5 * len(unsupported_variant_mentions)
        + 20 * gene_match
        + 5 * len(trait_matches)
        + 3 * len(variant_context_matches)
        + 2 * len(functional_matches)
        + len(clinical_matches)
    )

    if supported_exact_matches:
        tier = "exact_variant"
    elif gene_match and variant_context_matches:
        tier = "gene_variant_context"
    elif gene_match and trait_matches:
        tier = "gene_trait_context"
    elif gene_match:
        tier = "gene_only"
    else:
        tier = "unverified_search_hit"

    return {
        "tier": tier,
        "score": int(score),
        "score_meaning": "retrieval relevance only; not evidence strength",
        "matched_gene": gene_match,
        "matched_exact_identifiers": supported_exact_matches,
        "unsupported_variant_mentions": unsupported_variant_mentions,
        "matched_traits": trait_matches,
        "matched_variant_context_terms": variant_context_matches,
        "matched_functional_terms": functional_matches,
        "matched_clinical_terms": clinical_matches,
        "discovery_strategies": sorted(set(discovery_strategies)),
    }


def linked_pmids_by_variant(
    records: list[dict[str, object]],
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}

    for record in records:
        variant = record.get("variant")
        evidence = record.get("evidence")

        if not isinstance(variant, dict) or not isinstance(evidence, dict):
            raise ValueError("Linked PubMed record is malformed")

        variant_key = str(variant.get("variant_key", ""))

        if not variant_key or variant_key in result:
            raise ValueError("Linked PubMed records require unique variant keys")

        result[variant_key] = {
            str(value) for value in evidence.get("pmids", []) or []
        }

    return result
