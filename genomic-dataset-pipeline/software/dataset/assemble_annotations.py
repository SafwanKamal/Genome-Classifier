from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .build_features import load_contract
from .common import read_table, require_columns, write_table


def unique_key(
    frame: pd.DataFrame,
    key: str,
    source: str,
) -> pd.DataFrame:
    require_columns(frame, [key], source)

    if frame[key].isna().any():
        raise ValueError(f"{source} contains missing {key} values")

    if frame[key].duplicated().any():
        examples = (
            frame.loc[frame[key].duplicated(keep=False), key]
            .head(5)
            .tolist()
        )
        raise ValueError(
            f"{source} contains duplicate {key} values; "
            f"examples: {examples}"
        )

    return frame


def annotation_columns(
    frame: pd.DataFrame,
    key: str,
) -> list[str]:
    excluded = {
        key,
        "dbnsfp_match",
    }

    return [
        column
        for column in frame.columns
        if column not in excluded
    ]


def merge_annotation(
    base: pd.DataFrame,
    annotation: pd.DataFrame,
    key: str,
    source: str,
    validation: str,
) -> pd.DataFrame:
    annotation = unique_key(annotation.copy(), key, source)
    columns = annotation_columns(annotation, key)

    overlapping = sorted(
        set(columns) & set(base.columns)
    )

    if overlapping:
        raise ValueError(
            f"{source} would overwrite columns: "
            f"{', '.join(overlapping)}"
        )

    return base.merge(
        annotation[[key, *columns]],
        how="left",
        on=key,
        validate=validation,
    )


def assemble(
    variants: pd.DataFrame,
    dbnsfp: pd.DataFrame,
    genes: pd.DataFrame,
) -> pd.DataFrame:
    result = unique_key(
        variants.copy(),
        "variant_key",
        "ClinVar variants",
    )

    require_columns(
        result,
        ["gene"],
        "ClinVar variants",
    )

    result = merge_annotation(
        base=result,
        annotation=dbnsfp,
        key="variant_key",
        source="dbNSFP variant scores",
        validation="one_to_one",
    )

    result = merge_annotation(
        base=result,
        annotation=genes,
        key="gene",
        source="dbNSFP gene scores",
        validation="many_to_one",
    )

    return result


def coverage_report(
    frame: pd.DataFrame,
    contract: dict,
) -> dict[str, object]:
    raw_columns = sorted(
        {
            column
            for feature in contract["features"]
            for column in feature["raw_columns"]
        }
    )

    require_columns(
        frame,
        raw_columns,
        "assembled annotations",
    )

    return {
        "variant_count": int(len(frame)),
        "raw_feature_coverage": {
            column: {
                "nonmissing_count": int(
                    frame[column].notna().sum()
                ),
                "nonmissing_fraction": float(
                    frame[column].notna().mean()
                ),
            }
            for column in raw_columns
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble version-1 ClinVar, dbNSFP variant, "
            "and dbNSFP gene annotations."
        )
    )

    parser.add_argument(
        "--variants",
        type=Path,
        default=Path(
            "data/interim/clinvar_labeled.parquet"
        ),
    )

    parser.add_argument(
        "--dbnsfp",
        type=Path,
        default=Path(
            "data/interim/dbnsfp_variant_scores.parquet"
        ),
    )

    parser.add_argument(
        "--genes",
        type=Path,
        default=Path(
            "data/interim/dbnsfp_gene_scores.parquet"
        ),
    )

    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("config/features.yaml"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/interim/variants_annotated.parquet"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "data/interim/annotation_coverage_report.json"
        ),
    )

    args = parser.parse_args()

    assembled = assemble(
        variants=read_table(args.variants),
        dbnsfp=read_table(args.dbnsfp),
        genes=read_table(args.genes),
    )

    contract = load_contract(args.contract)
    report = coverage_report(assembled, contract)

    write_table(assembled, args.output)

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.report.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"Saved {len(assembled):,} assembled variants "
        f"to {args.output}"
    )
    print(
        f"Saved annotation coverage report "
        f"to {args.report}"
    )


if __name__ == "__main__":
    main()