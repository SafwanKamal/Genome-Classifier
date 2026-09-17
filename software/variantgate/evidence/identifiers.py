from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd


VARIATION_ID_COLUMNS = (
    "clinvar_variation_id",
    "variation_id",
    "VariationID",
    "variationid",
    "clinvar_id",
    "ClinVarVariationID",
)

VCV_PATTERN = re.compile(r"^VCV0*(\d+)(?:\.\d+)?$", re.IGNORECASE)
LABELED_ID_PATTERN = re.compile(
    r"(?:variation(?:_?id)?|clinvar)\s*[:=_-]\s*0*(\d+)",
    re.IGNORECASE,
)


def parse_variation_id(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value if value > 0 else None

    if isinstance(value, float):
        if value.is_integer() and value > 0:
            return int(value)

        return None

    text = str(value).strip()

    if text.isdigit():
        identifier = int(text)
        return identifier if identifier > 0 else None

    vcv_match = VCV_PATTERN.fullmatch(text)

    if vcv_match:
        identifier = int(vcv_match.group(1))
        return identifier if identifier > 0 else None

    labeled_match = LABELED_ID_PATTERN.search(text)

    if labeled_match:
        identifier = int(labeled_match.group(1))
        return identifier if identifier > 0 else None

    return None


def resolve_variation_id_column(
    columns: Iterable[str],
    requested_column: str | None,
) -> str | None:
    available_columns = {str(column) for column in columns}

    if requested_column is not None:
        if requested_column not in available_columns:
            raise ValueError(
                f"Requested variation-ID column {requested_column!r} "
                "is not present"
            )

        return requested_column

    for candidate in VARIATION_ID_COLUMNS:
        if candidate in available_columns:
            return candidate

    return None
