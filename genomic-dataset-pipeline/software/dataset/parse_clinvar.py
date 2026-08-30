from __future__ import annotations

import argparse
import gzip
from pathlib import Path
from typing import Iterator, TextIO

import pandas as pd

from .common import make_variant_key, normalize_chrom, write_table


POSITIVE = {"pathogenic", "likely_pathogenic", "pathogenic/likely_pathogenic"}
NEGATIVE = {"benign", "likely_benign", "benign/likely_benign"}
MISSENSE_SO = "SO:0001583"


def open_text(path: Path) -> TextIO:
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open("r", encoding="utf-8")


def parse_info(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in text.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
        elif item:
            result[item] = "true"
    return result


def allele_value(value: str | None, allele_index: int, allele_count: int) -> str:
    if value is None:
        return ""
    values = value.split(",")
    return values[allele_index] if len(values) == allele_count else value


def classify_significance(value: str) -> int | None:
    normalized = value.strip().lower().replace(" ", "_")
    if normalized in POSITIVE:
        return 1
    if normalized in NEGATIVE:
        return 0
    return None


def review_stars(value: str) -> int:
    normalized = value.lower().replace(" ", "_")
    if "no_assertion_criteria_provided" in normalized or "no_assertion_provided" in normalized:
        return 0
    if "practice_guideline" in normalized:
        return 4
    if "reviewed_by_expert_panel" in normalized:
        return 3
    if "multiple_submitters" in normalized and "no_conflicts" in normalized:
        return 2
    if "criteria_provided" in normalized:
        return 1
    return 0


def gene_symbol(geneinfo: str) -> str | None:
    symbols = []
    for entry in geneinfo.split("|"):
        symbol = entry.split(":", 1)[0].strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols[0] if len(symbols) == 1 else None


def is_missense(info: dict[str, str]) -> bool:
    molecular = info.get("MC", "")
    return MISSENSE_SO in molecular or "missense_variant" in molecular.lower()


def rows_from_vcf(path: Path, min_review_stars: int = 1) -> Iterator[dict[str, object]]:
    with open_text(path) as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 8:
                raise ValueError(f"Malformed VCF row at line {line_number}")
            chrom, pos_text, record_id, ref, alt_text, _qual, filter_value, info_text = fields[:8]
            if filter_value not in {"PASS", "."}:
                continue
            if len(ref) != 1 or ref.upper() not in "ACGT":
                continue

            info = parse_info(info_text)
            if not is_missense(info):
                continue

            alts = alt_text.split(",")
            # Keep the initial cohort allele-unambiguous. Multiallelic rows require
            # header-aware Number=A/G decoding and should be normalized upstream.
            if len(alts) != 1:
                continue
            for allele_index, alt in enumerate(alts):
                if len(alt) != 1 or alt.upper() not in "ACGT":
                    continue
                significance = allele_value(info.get("CLNSIG"), allele_index, len(alts))
                label = classify_significance(significance)
                if label is None:
                    continue
                review = allele_value(info.get("CLNREVSTAT"), allele_index, len(alts))
                stars = review_stars(review)
                if stars < min_review_stars:
                    continue
                gene = gene_symbol(allele_value(info.get("GENEINFO"), allele_index, len(alts)))
                if gene is None:
                    continue
                pos = int(pos_text)
                yield {
                    "variant_key": make_variant_key(chrom, pos, ref, alt),
                    "chrom": normalize_chrom(chrom),
                    "pos": pos,
                    "ref": ref.upper(),
                    "alt": alt.upper(),
                    "variation_id": info.get("ALLELEID") or info.get("CLNVID") or record_id,
                    "gene": gene,
                    "label": label,
                    "clinical_significance": significance,
                    "review_status": review,
                    "review_stars": stars,
                }


def parse_vcf(path: Path, min_review_stars: int = 1) -> pd.DataFrame:
    frame = pd.DataFrame(rows_from_vcf(path, min_review_stars))
    if frame.empty:
        return frame
    frame = frame.sort_values(["chrom", "pos", "ref", "alt"]).drop_duplicates("variant_key", keep=False)
    return frame.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract labeled missense SNVs from ClinVar VCF.")
    parser.add_argument("--input", type=Path, default=Path("data/raw/clinvar_grch38.vcf.gz"))
    parser.add_argument("--output", type=Path, default=Path("data/interim/clinvar_labeled.parquet"))
    parser.add_argument("--min-review-stars", type=int, choices=range(0, 5), default=1)
    args = parser.parse_args()

    frame = parse_vcf(args.input, args.min_review_stars)
    if frame.empty:
        raise SystemExit("No eligible variants found; check the VCF and filtering policy.")
    write_table(frame, args.output)
    counts = frame["label"].value_counts().to_dict()
    print(f"Saved {len(frame):,} variants to {args.output}; labels={counts}")


if __name__ == "__main__":
    main()
