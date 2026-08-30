from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Protocol

import pandas as pd

from .common import (
    KEY_COLUMNS,
    make_variant_key,
    normalize_chrom,
    read_table,
    require_columns,
    write_table,
)


DBNSFP_COLUMNS = {
    "gnomad_popmax_af": "gnomAD4.1_joint_POPMAX_AF",
    "cadd_phred": "CADD_phred",
    "revel_score": "REVEL_score",
    "alphamissense_score": "AlphaMissense_score",
    "esm1b_rankscore": "ESM1b_converted_rankscore",
    "sift_score": "SIFT_score",
    "polyphen2_hvar_score": "Polyphen2_HVAR_score",
    "mpc_score": "MPC_score",
    "phylop_100way": "phyloP100way_vertebrate",
    "phastcons_100way": "phastCons100way_vertebrate",
    "gerp_rs": "GERP++_RS",
}

TRANSCRIPT_COLUMNS = {
    "Ensembl_transcriptid",
    "genename",
    "MANE",
    "Ensembl_canonical",
    "APPRIS",
    "aaref",
    "aaalt",
    "Interpro_domain",
    *DBNSFP_COLUMNS.values(),
}


class TabixLike(Protocol):
    header: Iterable[str]

    def fetch(
        self,
        contig: str,
        start: int,
        end: int,
    ) -> Iterable[str]:
        ...


def split_values(value: object) -> list[str]:
    return str(value).split(";")


def is_missing(value: object) -> bool:
    return str(value).strip() in {
        "",
        ".",
        "NA",
        "nan",
        "None",
    }


def pick_aligned(
    value: object,
    index: int,
    transcript_count: int,
) -> str | None:
    values = split_values(value)

    if len(values) == 1:
        selected = values[0]
    elif len(values) == transcript_count and index < len(values):
        selected = values[index]
    elif index < len(values):
        selected = values[index]
    else:
        selected = values[0]

    return None if is_missing(selected) else selected


def choose_transcript(
    record: dict[str, str],
    preferred_gene: str | None = None,
) -> tuple[int, str]:
    transcripts = split_values(record["Ensembl_transcriptid"])
    genes = split_values(record["genename"])
    mane = split_values(record["MANE"])
    canonical = split_values(record["Ensembl_canonical"])
    appris = split_values(record["APPRIS"])

    candidates = list(range(len(transcripts)))

    if preferred_gene:
        matching = [
            index
            for index in candidates
            if (
                index < len(genes)
                and genes[index].upper() == preferred_gene.upper()
            )
        ]

        if matching:
            candidates = matching

    for index in candidates:
        if index < len(mane) and "select" in mane[index].lower():
            return index, "MANE Select"

    for index in candidates:
        if (
            index < len(canonical)
            and canonical[index].upper() in {"YES", "Y"}
        ):
            return index, "Ensembl canonical"

    for index in candidates:
        if (
            index < len(appris)
            and appris[index].lower().startswith("principal")
        ):
            return index, "APPRIS principal"

    return candidates[0], "first transcript"


def parse_number(value: str | None) -> float | None:
    if value is None or is_missing(value):
        return None

    try:
        return float(value)
    except ValueError:
        return None


GRANTHAM_ROWS = {
    "A": [
        0, 112, 111, 126, 195, 91, 107, 60, 86, 94,
        96, 106, 84, 113, 27, 99, 58, 148, 112, 64,
    ],
    "R": [
        112, 0, 86, 96, 180, 43, 54, 125, 29, 97,
        102, 26, 91, 97, 103, 110, 71, 101, 77, 96,
    ],
    "N": [
        111, 86, 0, 23, 139, 46, 42, 80, 68, 149,
        153, 94, 142, 158, 91, 46, 65, 174, 143, 133,
    ],
    "D": [
        126, 96, 23, 0, 154, 61, 45, 94, 81, 168,
        172, 101, 160, 177, 108, 65, 85, 181, 160, 152,
    ],
    "C": [
        195, 180, 139, 154, 0, 154, 170, 159, 174, 198,
        198, 202, 196, 205, 169, 112, 149, 215, 194, 192,
    ],
    "Q": [
        91, 43, 46, 61, 154, 0, 29, 87, 24, 109,
        113, 53, 101, 116, 76, 68, 42, 130, 99, 96,
    ],
    "E": [
        107, 54, 42, 45, 170, 29, 0, 98, 40, 134,
        138, 56, 126, 140, 93, 80, 65, 152, 122, 121,
    ],
    "G": [
        60, 125, 80, 94, 159, 87, 98, 0, 98, 135,
        138, 127, 127, 153, 42, 56, 59, 184, 147, 109,
    ],
    "H": [
        86, 29, 68, 81, 174, 24, 40, 98, 0, 94,
        99, 32, 87, 100, 77, 89, 47, 115, 83, 84,
    ],
    "I": [
        94, 97, 149, 168, 198, 109, 134, 135, 94, 0,
        5, 102, 10, 21, 95, 142, 89, 61, 33, 29,
    ],
    "L": [
        96, 102, 153, 172, 198, 113, 138, 138, 99, 5,
        0, 107, 15, 22, 98, 145, 92, 61, 36, 32,
    ],
    "K": [
        106, 26, 94, 101, 202, 53, 56, 127, 32, 102,
        107, 0, 95, 102, 103, 121, 78, 110, 85, 97,
    ],
    "M": [
        84, 91, 142, 160, 196, 101, 126, 127, 87, 10,
        15, 95, 0, 28, 87, 135, 81, 67, 36, 21,
    ],
    "F": [
        113, 97, 158, 177, 205, 116, 140, 153, 100, 21,
        22, 102, 28, 0, 114, 155, 103, 40, 22, 50,
    ],
    "P": [
        27, 103, 91, 108, 169, 76, 93, 42, 77, 95,
        98, 103, 87, 114, 0, 74, 38, 147, 110, 68,
    ],
    "S": [
        99, 110, 46, 65, 112, 68, 80, 56, 89, 142,
        145, 121, 135, 155, 74, 0, 58, 177, 144, 124,
    ],
    "T": [
        58, 71, 65, 85, 149, 42, 65, 59, 47, 89,
        92, 78, 81, 103, 38, 58, 0, 128, 92, 69,
    ],
    "W": [
        148, 101, 174, 181, 215, 130, 152, 184, 115, 61,
        61, 110, 67, 40, 147, 177, 128, 0, 37, 88,
    ],
    "Y": [
        112, 77, 143, 160, 194, 99, 122, 147, 83, 33,
        36, 85, 36, 22, 110, 144, 92, 37, 0, 55,
    ],
    "V": [
        64, 96, 133, 152, 192, 96, 121, 109, 84, 29,
        32, 97, 21, 50, 68, 124, 69, 88, 55, 0,
    ],
}

AA_ORDER = "ARNDCQEGHILKMFPSTWYV"

BLOSUM62_TEXT = """
   A  R  N  D  C  Q  E  G  H  I  L  K  M  F  P  S  T  W  Y  V
A  4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0
R -1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3
N -2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3
D -2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3
C  0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1
Q -1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2
E -1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2
G  0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3
H -2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3
I -1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3
L -1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1
K -1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2
M -1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1
F -2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1
P -1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2
S  1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2
T  0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0
W -3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3
Y -2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1
V  0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4
"""


def build_blosum62() -> dict[tuple[str, str], int]:
    lines = [
        line.split()
        for line in BLOSUM62_TEXT.strip().splitlines()
    ]

    header = lines[0]

    return {
        (row[0], amino): int(score)
        for row in lines[1:]
        for amino, score in zip(header, row[1:])
    }


BLOSUM62 = build_blosum62()


def substitution_scores(
    reference: str | None,
    alternate: str | None,
) -> tuple[int | None, int | None]:
    if reference is None or alternate is None:
        return None, None

    if reference not in AA_ORDER or alternate not in AA_ORDER:
        return None, None

    grantham = GRANTHAM_ROWS[reference][AA_ORDER.index(alternate)]
    blosum62 = BLOSUM62[(reference, alternate)]

    return grantham, blosum62


def header_columns(tabix: TabixLike) -> list[str]:
    lines = list(tabix.header)

    if not lines:
        raise ValueError("dbNSFP file has no tabix header")

    return lines[-1].lstrip("#").split("\t")


def row_dict(
    line: str,
    columns: list[str],
) -> dict[str, str]:
    values = line.rstrip("\n").split("\t")

    if len(values) != len(columns):
        raise ValueError(
            f"dbNSFP row has {len(values)} fields; "
            f"expected {len(columns)}"
        )

    return dict(zip(columns, values))


def record_matches(
    record: dict[str, str],
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
) -> bool:
    return (
        normalize_chrom(record["chr"]) == normalize_chrom(chrom)
        and int(record["pos(1-based)"]) == int(pos)
        and record["ref"].upper() == ref.upper()
        and record["alt"].upper() == alt.upper()
    )


def annotate_record(
    record: dict[str, str],
    preferred_gene: str | None,
) -> tuple[dict[str, object], str]:
    transcript_index, reason = choose_transcript(
        record,
        preferred_gene,
    )

    transcript_count = len(
        split_values(record["Ensembl_transcriptid"])
    )

    reference_aa = pick_aligned(
        record["aaref"],
        transcript_index,
        transcript_count,
    )

    alternate_aa = pick_aligned(
        record["aaalt"],
        transcript_index,
        transcript_count,
    )

    grantham, blosum62 = substitution_scores(
        reference_aa,
        alternate_aa,
    )

    domain = pick_aligned(
        record["Interpro_domain"],
        transcript_index,
        transcript_count,
    )

    result: dict[str, object] = {
        "selected_transcript": pick_aligned(
            record["Ensembl_transcriptid"],
            transcript_index,
            transcript_count,
        ),
        "dbnsfp_gene": pick_aligned(
            record["genename"],
            transcript_index,
            transcript_count,
        ),
        "transcript_selection": reason,
        "aaref": reference_aa,
        "aaalt": alternate_aa,
        "grantham_distance": grantham,
        "blosum62_score": blosum62,
        "interpro_domain_status": (
            "overlap"
            if domain
            else "no_overlap"
        ),
    }

    for output_name, source_name in DBNSFP_COLUMNS.items():
        selected = pick_aligned(
            record[source_name],
            transcript_index,
            transcript_count,
        )

        result[output_name] = parse_number(selected)

    return result, reason


def fetch_matching_records(
    tabix: TabixLike,
    columns: list[str],
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
) -> list[dict[str, str]]:
    normalized = normalize_chrom(chrom)
    contig = "M" if normalized == "MT" else normalized

    try:
        lines = tabix.fetch(
            contig,
            pos - 1,
            pos,
        )
    except (KeyError, ValueError):
        return []

    return [
        record
        for line in lines
        if record_matches(
            (
                record := row_dict(
                    line,
                    columns,
                )
            ),
            chrom,
            pos,
            ref,
            alt,
        )
    ]


def extract_variant_annotations(
    variants: pd.DataFrame,
    tabix: TabixLike,
    progress_every: int = 10_000,
) -> tuple[pd.DataFrame, dict[str, object]]:
    require_columns(
        variants,
        [
            *KEY_COLUMNS,
            "gene",
        ],
        "ClinVar variants",
    )

    columns = header_columns(tabix)

    required = {
        "chr",
        "pos(1-based)",
        "ref",
        "alt",
        *TRANSCRIPT_COLUMNS,
    }

    missing = sorted(required - set(columns))

    if missing:
        raise ValueError(
            "dbNSFP header is missing required columns: "
            + ", ".join(missing)
        )

    results: list[dict[str, object]] = []
    selection_counts: Counter[str] = Counter()
    matched = 0

    for number, variant in enumerate(
        variants.itertuples(index=False),
        start=1,
    ):
        chrom = str(variant.chrom)
        pos = int(variant.pos)
        ref = str(variant.ref)
        alt = str(variant.alt)
        gene = str(variant.gene)

        key = make_variant_key(
            chrom,
            pos,
            ref,
            alt,
        )

        records = fetch_matching_records(
            tabix,
            columns,
            chrom,
            pos,
            ref,
            alt,
        )

        output: dict[str, object] = {
            "variant_key": key,
            "dbnsfp_match": bool(records),
        }

        if records:
            record = next(
                (
                    item
                    for item in records
                    if gene.upper()
                    in {
                        value.upper()
                        for value in split_values(
                            item["genename"]
                        )
                    }
                ),
                records[0],
            )

            annotation, reason = annotate_record(
                record,
                gene,
            )

            output.update(annotation)
            selection_counts[reason] += 1
            matched += 1

        results.append(output)

        if (
            progress_every
            and number % progress_every == 0
        ):
            print(
                f"Queried {number:,}/{len(variants):,} "
                f"variants; matched {matched:,}",
                flush=True,
            )

    frame = pd.DataFrame(results)

    report: dict[str, object] = {
        "variant_count": int(len(variants)),
        "matched_variant_count": int(matched),
        "matched_fraction": (
            matched / len(variants)
            if len(variants)
            else 0.0
        ),
        "transcript_selection_counts": dict(
            selection_counts
        ),
        "feature_nonmissing_counts": {
            column: (
                int(frame[column].notna().sum())
                if column in frame
                else 0
            )
            for column in [
                *DBNSFP_COLUMNS,
                "grantham_distance",
                "blosum62_score",
            ]
        },
    }

    return frame, report


def summarize_variant_annotations(
    frame: pd.DataFrame,
) -> dict[str, object]:
    matched = int(
        frame["dbnsfp_match"]
        .fillna(False)
        .astype(bool)
        .sum()
    )

    if "transcript_selection" in frame:
        selection_counts = (
            frame["transcript_selection"]
            .dropna()
            .astype(str)
            .value_counts()
            .to_dict()
        )
    else:
        selection_counts = {}

    return {
        "variant_count": int(len(frame)),
        "matched_variant_count": matched,
        "matched_fraction": (
            matched / len(frame)
            if len(frame)
            else 0.0
        ),
        "transcript_selection_counts": selection_counts,
        "feature_nonmissing_counts": {
            column: (
                int(frame[column].notna().sum())
                if column in frame
                else 0
            )
            for column in [
                *DBNSFP_COLUMNS,
                "grantham_distance",
                "blosum62_score",
            ]
        },
    }


def extract_gene_annotations(
    path: Path,
    genes: set[str],
) -> pd.DataFrame:
    columns = [
        "Gene_name",
        "gnomAD_LOEUF",
        "gnomAD_MOEUF",
    ]

    frame = pd.read_csv(
        path,
        sep="\t",
        compression=(
            "gzip"
            if path.suffix == ".gz"
            else None
        ),
        usecols=columns,
        dtype=str,
        na_values=[
            ".",
            "",
        ],
        keep_default_na=True,
    )

    frame = frame.loc[
        frame["Gene_name"].isin(genes),
        columns,
    ].rename(
        columns={
            "Gene_name": "gene",
            "gnomAD_LOEUF": "gnomad_loeuf",
            "gnomAD_MOEUF": "gnomad_moeuf",
        }
    )

    frame["gnomad_loeuf"] = pd.to_numeric(
        frame["gnomad_loeuf"],
        errors="coerce",
    )

    frame["gnomad_moeuf"] = pd.to_numeric(
        frame["gnomad_moeuf"],
        errors="coerce",
    )

    return (
        frame
        .drop_duplicates("gene")
        .reset_index(drop=True)
    )


def open_tabix(path: Path) -> TabixLike:
    try:
        import pysam
    except ImportError as error:
        raise SystemExit(
            "pysam is required for dbNSFP extraction. "
            "Run this command through the WSL uv environment."
        ) from error

    # dbNSFP includes occasional non-ASCII annotation text.
    return pysam.TabixFile(
        str(path),
        encoding="utf-8",
    )


def extract_with_checkpoints(
    variants: pd.DataFrame,
    tabix: TabixLike,
    checkpoint_dir: Path,
    chunk_size: int,
) -> pd.DataFrame:
    checkpoint_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    chunks: list[pd.DataFrame] = []

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

        expected = variants.iloc[start:stop]

        expected_keys = [
            make_variant_key(
                chrom,
                pos,
                ref,
                alt,
            )
            for chrom, pos, ref, alt
            in expected[KEY_COLUMNS].itertuples(
                index=False,
                name=None,
            )
        ]

        if checkpoint.exists():
            chunk = read_table(checkpoint)

            checkpoint_keys = (
                chunk["variant_key"]
                .astype(str)
                .tolist()
            )

            if checkpoint_keys != expected_keys:
                raise ValueError(
                    f"Checkpoint {checkpoint} does not "
                    "match the current input cohort. "
                    "Remove the checkpoint directory "
                    "before restarting with a different cohort."
                )

            print(
                "Reused checkpoint through variant "
                f"{stop:,}/{len(variants):,}",
                flush=True,
            )

        else:
            chunk, _report = extract_variant_annotations(
                expected,
                tabix,
                progress_every=0,
            )

            write_table(
                chunk,
                checkpoint,
            )

            matched = int(
                chunk["dbnsfp_match"]
                .fillna(False)
                .astype(bool)
                .sum()
            )

            print(
                f"Checkpointed {stop:,}/{len(variants):,} "
                f"variants; chunk matches="
                f"{matched:,}/{len(chunk):,}",
                flush=True,
            )

        chunks.append(chunk)

    if not chunks:
        return pd.DataFrame()

    return pd.concat(
        chunks,
        ignore_index=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract indexed dbNSFP annotations "
            "for ClinVar variants."
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
        "--limit",
        type=int,
        help=(
            "Query only the first N variants "
            "for a smoke test."
        ),
    )

    parser.add_argument(
        "--progress-every",
        type=int,
        default=10_000,
    )

    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(
            "data/interim/dbnsfp_checkpoints"
        ),
        help=(
            "Directory containing resumable "
            "extraction chunks."
        ),
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1_000,
    )

    args = parser.parse_args()

    variants = read_table(args.variants)

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit(
                "--limit must be positive"
            )

        variants = (
            variants
            .head(args.limit)
            .copy()
        )

    if args.chunk_size <= 0:
        raise SystemExit(
            "--chunk-size must be positive"
        )

    tabix = open_tabix(args.dbnsfp)

    try:
        variant_scores = extract_with_checkpoints(
            variants,
            tabix,
            args.checkpoint_dir,
            args.chunk_size,
        )
    finally:
        close = getattr(
            tabix,
            "close",
            None,
        )

        if close:
            close()

    report = summarize_variant_annotations(
        variant_scores
    )

    gene_scores = extract_gene_annotations(
        args.gene_table,
        set(variants["gene"].astype(str)),
    )

    report["matched_gene_count"] = int(
        len(gene_scores)
    )

    report["gene_count"] = int(
        variants["gene"].nunique()
    )

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
        f"Saved {len(variant_scores):,} variant rows "
        f"to {args.variant_output}; "
        f"dbNSFP matches="
        f"{report['matched_variant_count']:,}"
    )

    print(
        f"Saved {len(gene_scores):,} gene rows "
        f"to {args.gene_output}"
    )

    print(
        f"Saved extraction report "
        f"to {args.report}"
    )


if __name__ == "__main__":
    main()