"""Repair legacy Allele-ID metadata using a coordinate-matched ClinVar VCF.

Writes a new Parquet and manifest; never rewrites frozen features or labels.
"""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path

import pandas as pd

from software.variantgate.evidence.pubmed import write_json
from software.variantgate.manifest import sha256_file


def repair(input_path: Path, vcf_path: Path, output_path: Path) -> dict:
    input_path, vcf_path, output_path = (
        p.resolve() for p in (input_path, vcf_path, output_path)
    )
    if output_path in {input_path, vcf_path} or output_path.exists():
        raise ValueError("Repair output must be a new file")
    if output_path.suffix != ".parquet":
        raise ValueError("Repair output must be .parquet")
    manifest_path = output_path.with_suffix(".repair.json")
    if manifest_path.exists():
        raise ValueError("Repair manifest already exists")
    frame = pd.read_parquet(input_path)
    if not {"variant_key", "gene", "variation_id"} <= set(frame.columns):
        raise ValueError("Input requires variant_key, gene and variation_id")
    if frame["variant_key"].duplicated().any():
        raise ValueError("Duplicate input variant keys")
    wanted = set(frame["variant_key"].astype(str))
    mapping = {}
    opener = gzip.open if vcf_path.suffix == ".gz" else open
    with opener(vcf_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("##reference=") and any(
                x in line.lower() for x in ("grch37", "hg19", "b37")
            ):
                raise ValueError(
                    "Source VCF reference contradicts declared GRCh38 assembly"
                )
            if line.startswith("#") or not line.strip():
                continue
            fields = line.rstrip().split("\t")
            if len(fields) < 8:
                raise ValueError("Malformed ClinVar VCF")
            chrom, pos, variation_id, ref, alt = fields[:5]
            chrom = chrom.removeprefix("chr")
            key = f"{chrom}:{int(pos)}:{ref.upper()}:{alt.upper()}"
            if key not in wanted:
                continue
            if key in mapping:
                raise ValueError("Ambiguous duplicate coordinate in source VCF: " + key)
            if not variation_id.isdigit() or int(variation_id) <= 0:
                raise ValueError("Invalid Variation ID in source VCF")
            info = dict(
                item.split("=", 1) for item in fields[7].split(";") if "=" in item
            )
            allele = info.get("ALLELEID")
            genes = [x.split(":")[0] for x in info.get("GENEINFO", "").split("|") if x]
            mapping[key] = (variation_id, allele, genes)
    missing = wanted - set(mapping)
    if missing:
        raise ValueError(
            f"Source VCF lacks {len(missing)} input variants; use the original cohort snapshot"
        )
    new_ids, alleles = [], []
    for row in frame.itertuples(index=False):
        variation_id, allele_id, genes = mapping[str(row.variant_key)]
        if str(row.gene) not in genes:
            raise ValueError("Source gene mismatch: " + str(row.variant_key))
        old_id = str(row.variation_id)
        if old_id not in {variation_id, allele_id}:
            raise ValueError(
                "Legacy ID does not match source Variation/Allele ID: "
                + str(row.variant_key)
            )
        new_ids.append(variation_id)
        alleles.append(allele_id)
    result = frame.copy()
    result["variation_id"] = pd.Series(new_ids, index=result.index, dtype="string")
    result["allele_id"] = pd.Series(alleles, index=result.index, dtype="string")
    unchanged = [c for c in frame if c not in {"variation_id", "allele_id"}]
    pd.testing.assert_frame_equal(frame[unchanged], result[unchanged])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(output_path, index=False)
    summary = {
        "status": "pass",
        "variant_number": len(frame),
        "changed_variation_id_number": int(
            (
                frame["variation_id"].astype(str) != result["variation_id"].astype(str)
            ).sum()
        ),
        "features_labels_splits_unchanged": True,
        "inputs": {
            "variants": {"path": str(input_path), "sha256": sha256_file(input_path)},
            "clinvar_vcf": {"path": str(vcf_path), "sha256": sha256_file(vcf_path)},
        },
        "output": {"path": str(output_path), "sha256": sha256_file(output_path)},
        "identity_basis": "Exact coordinate/ref/alt and gene match in the supplied GRCh38 VCF; "
        "live evidence must still pass the pipeline's independent source checks.",
    }
    write_json(manifest_path, summary)
    return summary


def main() -> None:
    import json

    parser = argparse.ArgumentParser(
        description="Repair ClinVar IDs without changing model inputs"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--clinvar-vcf", type=Path, required=True)
    parser.add_argument("--assembly", choices=["GRCh38"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.input, args.clinvar_vcf, args.output), indent=2))


if __name__ == "__main__":
    main()
