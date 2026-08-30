from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from .common import (
    KEY_COLUMNS,
    make_variant_key,
    read_table,
    write_table,
)

from .extract_dbnsfp import (
    extract_gene_annotations,
    extract_variant_annotations,
    open_tabix,
    summarize_variant_annotations,
)


_THREAD_STATE = threading.local()


def worker_tabix(path: Path):
    """
    Give each worker its own TabixFile.

    A single TabixFile must not be shared between threads because its
    seek/decompression state is mutable.
    """
    resolved = str(path.resolve())

    tabix = getattr(
        _THREAD_STATE,
        "tabix",
        None,
    )

    opened_path = getattr(
        _THREAD_STATE,
        "path",
        None,
    )

    if tabix is None or opened_path != resolved:
        if tabix is not None:
            tabix.close()

        tabix = open_tabix(path)

        _THREAD_STATE.tabix = tabix
        _THREAD_STATE.path = resolved

    return tabix


def expected_variant_keys(
    frame: pd.DataFrame,
) -> list[str]:
    return [
        make_variant_key(
            chrom,
            pos,
            ref,
            alt,
        )
        for chrom, pos, ref, alt
        in frame[KEY_COLUMNS].itertuples(
            index=False,
            name=None,
        )
    ]


def write_checkpoint_atomic(
    frame: pd.DataFrame,
    destination: Path,
) -> None:
    """
    Write to a temporary file first so an interrupted write does not
    leave a corrupt checkpoint with the final filename.
    """
    temporary = destination.with_name(
        destination.stem + ".tmp.parquet"
    )

    write_table(
        frame,
        temporary,
    )

    temporary.replace(destination)


def query_chunk(
    variants: pd.DataFrame,
    dbnsfp_path: Path,
    checkpoint: Path,
) -> tuple[pd.DataFrame, int]:
    tabix = worker_tabix(dbnsfp_path)

    result, _report = extract_variant_annotations(
        variants,
        tabix,
        progress_every=0,
    )

    write_checkpoint_atomic(
        result,
        checkpoint,
    )

    matched = int(
        result["dbnsfp_match"]
        .fillna(False)
        .astype(bool)
        .sum()
    )

    return result, matched


def extract_parallel(
    variants: pd.DataFrame,
    dbnsfp_path: Path,
    checkpoint_dir: Path,
    chunk_size: int,
    workers: int,
) -> pd.DataFrame:
    checkpoint_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    completed: dict[int, pd.DataFrame] = {}

    pending: list[
        tuple[
            int,
            int,
            Path,
            pd.DataFrame,
        ]
    ] = []

    for start in range(
        0,
        len(variants),
        chunk_size,
    ):
        stop = min(
            start + chunk_size,
            len(variants),
        )

        checkpoint = (
            checkpoint_dir
            / (
                f"variants_{start:07d}_"
                f"{stop - 1:07d}.parquet"
            )
        )

        expected = (
            variants
            .iloc[start:stop]
            .copy()
        )

        expected_keys = expected_variant_keys(
            expected
        )

        if checkpoint.exists():
            chunk = read_table(checkpoint)

            actual_keys = (
                chunk["variant_key"]
                .astype(str)
                .tolist()
            )

            if actual_keys != expected_keys:
                raise ValueError(
                    f"Checkpoint {checkpoint} does not "
                    "match the current ClinVar cohort. "
                    "Use a different --checkpoint-dir or "
                    "remove the incompatible checkpoints."
                )

            completed[start] = chunk

            print(
                f"Reused checkpoint through "
                f"{stop:,}/{len(variants):,}",
                flush=True,
            )

        else:
            pending.append(
                (
                    start,
                    stop,
                    checkpoint,
                    expected,
                )
            )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as executor:
        futures = {
            executor.submit(
                query_chunk,
                expected,
                dbnsfp_path,
                checkpoint,
            ): (
                start,
                stop,
            )
            for (
                start,
                stop,
                checkpoint,
                expected,
            ) in pending
        }

        for future in as_completed(futures):
            start, stop = futures[future]

            chunk, matched = future.result()

            completed[start] = chunk

            print(
                f"Checkpointed "
                f"{stop:,}/{len(variants):,}; "
                f"chunk matches="
                f"{matched:,}/{len(chunk):,}; "
                f"workers={workers}",
                flush=True,
            )

    ordered = [
        completed[start]
        for start in sorted(completed)
    ]

    if not ordered:
        return pd.DataFrame()

    return pd.concat(
        ordered,
        ignore_index=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract dbNSFP annotations using "
            "independent indexed query workers."
        )
    )

    parser.add_argument(
        "--variants",
        type=Path,
        default=Path(
            "data/interim/"
            "clinvar_labeled.parquet"
        ),
    )

    parser.add_argument(
        "--dbnsfp",
        type=Path,
        default=Path(
            "data/raw/dbnsfp/"
            "dbNSFP5.4a_grch38.gz"
        ),
    )

    parser.add_argument(
        "--gene-table",
        type=Path,
        default=Path(
            "data/raw/dbnsfp/"
            "dbNSFP5.4_gene.gz"
        ),
    )

    parser.add_argument(
        "--variant-output",
        type=Path,
        default=Path(
            "data/interim/"
            "dbnsfp_variant_scores.parquet"
        ),
    )

    parser.add_argument(
        "--gene-output",
        type=Path,
        default=Path(
            "data/interim/"
            "dbnsfp_gene_scores.parquet"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "data/interim/"
            "dbnsfp_extraction_report.json"
        ),
    )

    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(
            "data/interim/"
            "dbnsfp_checkpoints"
        ),
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1_000,
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--limit",
        type=int,
    )

    args = parser.parse_args()

    if args.chunk_size <= 0:
        raise SystemExit(
            "--chunk-size must be positive"
        )

    if args.workers <= 0:
        raise SystemExit(
            "--workers must be positive"
        )

    if (
        args.limit is not None
        and args.limit <= 0
    ):
        raise SystemExit(
            "--limit must be positive"
        )

    variants = read_table(
        args.variants
    )

    if args.limit is not None:
        variants = (
            variants
            .head(args.limit)
            .copy()
        )

    variant_scores = extract_parallel(
        variants=variants,
        dbnsfp_path=args.dbnsfp,
        checkpoint_dir=args.checkpoint_dir,
        chunk_size=args.chunk_size,
        workers=args.workers,
    )

    report = summarize_variant_annotations(
        variant_scores
    )

    gene_scores = extract_gene_annotations(
        args.gene_table,
        set(
            variants["gene"]
            .astype(str)
        ),
    )

    report["matched_gene_count"] = int(
        len(gene_scores)
    )

    report["gene_count"] = int(
        variants["gene"].nunique()
    )

    report["workers"] = args.workers
    report["chunk_size"] = args.chunk_size

    write_table(
        variant_scores,
        args.variant_output,
    )

    write_table(
        gene_scores,
        args.gene_output,
    )

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.report.write_text(
        json.dumps(
            report,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Saved {len(variant_scores):,} "
        f"variant rows to "
        f"{args.variant_output}; "
        f"dbNSFP matches="
        f"{report['matched_variant_count']:,}"
    )

    print(
        f"Saved {len(gene_scores):,} "
        f"gene rows to "
        f"{args.gene_output}"
    )

    print(
        f"Saved extraction report to "
        f"{args.report}"
    )


if __name__ == "__main__":
    main()