from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from software.dataset.annotate_variants import merge_annotations
from software.dataset.build_features import build_feature_tables, load_contract
from software.dataset.extract_dbnsfp import (
    annotate_record,
    extract_variant_annotations,
    substitution_scores,
)
from software.dataset.parse_clinvar import parse_vcf
from software.dataset.split_by_gene import assign_gene_splits
from software.dataset.validate_dataset import validate


ROOT = Path(__file__).resolve().parents[1]


def synthetic_annotated() -> pd.DataFrame:
    rows = []
    for gene_index in range(12):
        for label in (0, 1):
            value = (gene_index * 2 + label + 1) / 25
            rows.append(
                {
                    "variant_key": f"{gene_index + 1}:{100 + label}:A:G",
                    "chrom": str(gene_index + 1),
                    "pos": 100 + label,
                    "ref": "A",
                    "alt": "G",
                    "variation_id": gene_index * 2 + label,
                    "gene": f"GENE{gene_index:02d}",
                    "label": label,
                    "clinical_significance": "Pathogenic" if label else "Benign",
                    "review_status": "criteria_provided,_single_submitter",
                    "review_stars": 1,
                    "gnomad_popmax_af": value / 1000,
                    "cadd_phred": value * 50,
                    "revel_score": value,
                    "alphamissense_score": value,
                    "esm1b_rankscore": value,
                    "spliceai_ds_ag": value,
                    "spliceai_ds_al": value / 2,
                    "spliceai_ds_dg": value / 3,
                    "spliceai_ds_dl": value / 4,
                    "sift_score": 1 - value,
                    "polyphen2_hvar_score": value,
                    "mpc_score": value * 5,
                    "phylop_100way": gene_index - 4,
                    "phastcons_100way": value,
                    "gerp_rs": gene_index / 2,
                    "grantham_distance": value * 215,
                    "blosum62_score": 11 - value * 15,
                    "gnomad_mis_z": gene_index - 2,
                    "gnomad_loeuf": 2 - value,
                    "gnomad_moeuf": 2 - value,
                    "interpro_domain_status": "overlap" if label else "no_overlap",
                }
            )
    frame = pd.DataFrame(rows)
    frame.loc[frame.index[-1], "cadd_phred"] = np.nan
    return frame


def test_parse_clinvar_filters_labels_review_and_consequence() -> None:
    frame = parse_vcf(ROOT / "tests/fixtures/clinvar_sample.vcf", min_review_stars=1)
    assert frame["variant_key"].tolist() == ["1:100:A:G", "1:200:C:T"]
    assert frame["label"].tolist() == [1, 0]
    assert frame["review_stars"].tolist() == [1, 2]


def test_annotation_join_rejects_ambiguous_rows() -> None:
    base = pd.DataFrame({"variant_key": ["1:1:A:G"], "label": [1], "gene": ["A"]})
    annotation = pd.DataFrame({"variant_key": ["1:1:A:G"], "score": [0.5]})
    merged = merge_annotations(base, [("scores", annotation)])
    assert merged.loc[0, "score"] == 0.5


def dbnsfp_record() -> dict[str, str]:
    record = {
        "chr": "1",
        "pos(1-based)": "100",
        "ref": "A",
        "alt": "G",
        "Ensembl_transcriptid": "ENST_OTHER;ENST_MANE",
        "genename": "GENE1;GENE1",
        "MANE": ".;Select",
        "Ensembl_canonical": "YES;.",
        "APPRIS": "principal_1;.",
        "aaref": "A;A",
        "aaalt": "V;V",
        "Interpro_domain": ".;IPR000001",
    }
    for source in {
        "gnomAD4.1_joint_POPMAX_AF",
        "CADD_phred",
        "REVEL_score",
        "AlphaMissense_score",
        "ESM1b_converted_rankscore",
        "SIFT_score",
        "Polyphen2_HVAR_score",
        "MPC_score",
        "phyloP100way_vertebrate",
        "phastCons100way_vertebrate",
        "GERP++_RS",
    }:
        record[source] = "0.1;0.9"
    return record


def test_dbnsfp_selects_mane_and_derives_substitution_features() -> None:
    annotation, reason = annotate_record(dbnsfp_record(), "GENE1")
    assert reason == "MANE Select"
    assert annotation["selected_transcript"] == "ENST_MANE"
    assert annotation["revel_score"] == 0.9
    assert annotation["grantham_distance"] == 64
    assert annotation["blosum62_score"] == 0
    assert annotation["interpro_domain_status"] == "overlap"
    assert substitution_scores("C", "W") == (215, -2)


def test_dbnsfp_indexed_extraction_keeps_unmatched_variants() -> None:
    record = dbnsfp_record()
    columns = list(record)

    class FakeTabix:
        header = ["#" + "\t".join(columns)]

        def fetch(self, contig: str, start: int, end: int):
            if (contig, start, end) == ("1", 99, 100):
                return ["\t".join(record[column] for column in columns)]
            return []

    variants = pd.DataFrame(
        [
            {"chrom": "1", "pos": 100, "ref": "A", "alt": "G", "gene": "GENE1"},
            {"chrom": "1", "pos": 200, "ref": "C", "alt": "T", "gene": "GENE2"},
        ]
    )
    annotations, report = extract_variant_annotations(variants, FakeTabix(), progress_every=0)
    assert annotations["variant_key"].tolist() == ["1:100:A:G", "1:200:C:T"]
    assert annotations["dbnsfp_match"].tolist() == [True, False]
    assert report["matched_variant_count"] == 1


def test_gene_split_feature_build_and_validation() -> None:
    annotated = synthetic_annotated()
    splits = assign_gene_splits(annotated, seed=7)
    assert set(splits["split"]) == {"train", "validation", "test"}
    for left, right in [("train", "validation"), ("train", "test"), ("validation", "test")]:
        left_genes = set(splits.loc[splits["split"] == left, "gene"])
        right_genes = set(splits.loc[splits["split"] == right, "gene"])
        assert not left_genes & right_genes

    contract = load_contract(ROOT / "config/features.yaml")
    float_frame, int8_frame, preprocessing, audit = build_feature_tables(annotated, splits, contract)
    feature_ids = [feature["id"] for feature in contract["features"]]

    assert len(feature_ids) == 16
    assert int8_frame.columns[-16:].tolist() == feature_ids
    assert not float_frame[feature_ids].isna().any().any()
    assert ((float_frame[feature_ids] >= 0) & (float_frame[feature_ids] <= 1)).all().all()
    assert ((int8_frame[feature_ids] >= -127) & (int8_frame[feature_ids] <= 127)).all().all()
    assert int8_frame[feature_ids].dtypes.apply(lambda dtype: dtype == np.dtype("int8")).all()
    assert preprocessing["fit_split"] == "train"
    assert audit.filter(like="__missing").shape[1] == 16

    report = validate(int8_frame, feature_ids)
    assert report["status"] == "pass", report["errors"]
