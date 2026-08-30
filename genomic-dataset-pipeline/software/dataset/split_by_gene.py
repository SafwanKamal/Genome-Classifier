from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .common import read_table, require_columns, write_table


@dataclass(frozen=True)
class SplitTarget:
    name: str
    fraction: float


def assign_gene_splits(
    frame: pd.DataFrame,
    seed: int = 42,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> pd.DataFrame:
    require_columns(frame, ["variant_key", "gene", "label"], "annotated variants")
    test_fraction = 1.0 - train_fraction - validation_fraction
    if min(train_fraction, validation_fraction, test_fraction) <= 0:
        raise ValueError("Train, validation, and test fractions must all be positive.")
    if not np.isclose(train_fraction + validation_fraction + test_fraction, 1.0):
        raise ValueError("Split fractions must sum to one.")

    gene_stats = (
        frame.groupby("gene", sort=False)["label"]
        .agg(total="size", positives="sum")
        .reset_index()
    )
    if len(gene_stats) < 3:
        raise ValueError("At least three distinct genes are required for gene-disjoint splitting.")

    rng = np.random.default_rng(seed)
    gene_stats["tie_break"] = rng.random(len(gene_stats))
    gene_stats = gene_stats.sort_values(["total", "tie_break"], ascending=[False, True])

    targets = [
        SplitTarget("train", train_fraction),
        SplitTarget("validation", validation_fraction),
        SplitTarget("test", test_fraction),
    ]
    total_rows = float(gene_stats["total"].sum())
    total_positives = float(gene_stats["positives"].sum())
    total_negatives = total_rows - total_positives
    state = {
        target.name: {"rows": 0.0, "positives": 0.0, "negatives": 0.0, "genes": []}
        for target in targets
    }

    # Seed every split with one gene, then greedily minimize row and class-target error.
    for index, row in enumerate(gene_stats.itertuples(index=False)):
        if index < len(targets):
            chosen = targets[index].name
        else:
            scores: dict[str, float] = {}
            for target in targets:
                row_goal = max(total_rows * target.fraction, 1.0)
                pos_goal = max(total_positives * target.fraction, 1.0)
                neg_goal = max(total_negatives * target.fraction, 1.0)
                row_fill = state[target.name]["rows"] / row_goal
                pos_fill = state[target.name]["positives"] / pos_goal
                neg_fill = state[target.name]["negatives"] / neg_goal
                scores[target.name] = 0.5 * row_fill + 0.25 * pos_fill + 0.25 * neg_fill
            chosen = min(scores, key=scores.get)
        state[chosen]["rows"] += float(row.total)
        state[chosen]["positives"] += float(row.positives)
        state[chosen]["negatives"] += float(row.total - row.positives)
        state[chosen]["genes"].append(row.gene)

    gene_to_split = {
        gene: split_name
        for split_name, values in state.items()
        for gene in values["genes"]
    }
    result = frame[["variant_key", "gene", "label"]].copy()
    result["split"] = result["gene"].map(gene_to_split)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic gene-disjoint dataset splits.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/interim/splits.parquet"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    args = parser.parse_args()

    frame = read_table(args.input)
    result = assign_gene_splits(frame, args.seed, args.train_fraction, args.validation_fraction)
    write_table(result, args.output)
    summary = result.groupby("split").agg(variants=("variant_key", "size"), genes=("gene", "nunique"), positives=("label", "sum"))
    print(summary.to_string())
    print(f"Saved split assignments to {args.output}")


if __name__ == "__main__":
    main()
