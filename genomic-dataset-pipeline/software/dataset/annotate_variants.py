from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .common import KEY_COLUMNS, make_variant_key, read_table, require_columns, write_table


def ensure_variant_key(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    result = frame.copy()
    if "variant_key" not in result.columns:
        require_columns(result, KEY_COLUMNS, source)
        result["variant_key"] = [
            make_variant_key(chrom, pos, ref, alt)
            for chrom, pos, ref, alt in result[KEY_COLUMNS].itertuples(index=False, name=None)
        ]
    result["variant_key"] = result["variant_key"].astype(str)
    if result["variant_key"].duplicated().any():
        examples = result.loc[result["variant_key"].duplicated(keep=False), "variant_key"].head(5).tolist()
        raise ValueError(
            f"{source} contains multiple rows per variant. Resolve transcript/allele selection first; examples: {examples}"
        )
    return result


def merge_annotations(base: pd.DataFrame, annotation_frames: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    result = ensure_variant_key(base, "base variants")
    for name, frame in annotation_frames:
        annotation = ensure_variant_key(frame, name)
        duplicated_columns = (set(result.columns) & set(annotation.columns)) - {"variant_key"}
        if duplicated_columns:
            raise ValueError(
                f"{name} would overwrite existing columns: {', '.join(sorted(duplicated_columns))}"
            )
        result = result.merge(annotation, how="left", on="variant_key", validate="one_to_one")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join one-row-per-allele annotation tables to labeled ClinVar variants."
    )
    parser.add_argument("--variants", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/interim/variants_annotated.parquet"))
    args = parser.parse_args()

    base = read_table(args.variants)
    annotations = [(str(path), read_table(path)) for path in args.annotations]
    merged = merge_annotations(base, annotations)
    write_table(merged, args.output)
    annotation_columns = len(merged.columns) - len(base.columns)
    print(f"Saved {len(merged):,} variants with {annotation_columns} joined columns to {args.output}")


if __name__ == "__main__":
    main()

