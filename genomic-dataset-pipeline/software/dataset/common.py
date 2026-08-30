from __future__ import annotations

from pathlib import Path

import pandas as pd


KEY_COLUMNS = ["chrom", "pos", "ref", "alt"]
META_COLUMNS = [
    "variant_key",
    "chrom",
    "pos",
    "ref",
    "alt",
    "variation_id",
    "gene",
    "label",
    "clinical_significance",
    "review_status",
    "review_stars",
]


def normalize_chrom(value: object) -> str:
    chrom = str(value).strip()
    if chrom.lower().startswith("chr"):
        chrom = chrom[3:]
    return chrom.upper().replace("M", "MT") if chrom.upper() in {"M", "MT"} else chrom


def make_variant_key(chrom: object, pos: object, ref: object, alt: object) -> str:
    return f"{normalize_chrom(chrom)}:{int(pos)}:{str(ref).upper()}:{str(alt).upper()}"


def read_table(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    suffixes = source.suffixes
    if source.suffix == ".parquet":
        return pd.read_parquet(source)
    if source.suffix in {".tsv", ".txt"} or suffixes[-2:] == [".tsv", ".gz"]:
        return pd.read_csv(source, sep="\t")
    if source.suffix == ".gz" and source.name.endswith(".csv.gz"):
        return pd.read_csv(source)
    if source.suffix == ".csv":
        return pd.read_csv(source)
    raise ValueError(f"Unsupported table format: {source}")


def write_table(frame: pd.DataFrame, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix == ".parquet":
        frame.to_parquet(destination, index=False)
    elif destination.suffix in {".tsv", ".txt"}:
        frame.to_csv(destination, sep="\t", index=False)
    elif destination.suffix == ".csv":
        frame.to_csv(destination, index=False)
    else:
        raise ValueError(f"Unsupported table format: {destination}")


def require_columns(frame: pd.DataFrame, columns: list[str], source: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{source} is missing required columns: {', '.join(missing)}")

