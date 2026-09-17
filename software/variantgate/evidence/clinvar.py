from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


EUTILS_SUMMARY_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary_path.replace(path)


def chunks(values: Sequence[int], size: int) -> list[list[int]]:
    return [
        list(values[start : start + size])
        for start in range(0, len(values), size)
    ]


@dataclass(frozen=True, slots=True)
class ClinVarSummary:
    variation_id: int
    retrieved_at: str
    request_url: str
    cached: bool
    raw_summary: dict[str, object] | None

    @property
    def found(self) -> bool:
        return self.raw_summary is not None


class ClinVarClient:
    def __init__(
        self,
        cache_directory: Path,
        email: str | None = None,
        api_key_environment: str = "NCBI_API_KEY",
        batch_size: int = 100,
        timeout_seconds: float = 30.0,
        retry_number: int = 4,
        refresh: bool = False,
    ) -> None:
        if batch_size <= 0 or batch_size > 200:
            raise ValueError("batch_size must be between 1 and 200")

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        if retry_number < 0:
            raise ValueError("retry_number cannot be negative")

        self._cache_directory = cache_directory.resolve()
        self._email = email
        self._api_key = os.environ.get(api_key_environment)
        self._batch_size = batch_size
        self._timeout_seconds = timeout_seconds
        self._retry_number = retry_number
        self._refresh = refresh
        self._minimum_interval_seconds = 0.11 if self._api_key else 0.34
        self._last_request_time = 0.0
        self._request_attempt_number = 0
        self._successful_request_number = 0

    @property
    def cache_directory(self) -> Path:
        return self._cache_directory

    @property
    def api_key_used(self) -> bool:
        return self._api_key is not None

    @property
    def request_attempt_number(self) -> int:
        return self._request_attempt_number

    @property
    def successful_request_number(self) -> int:
        return self._successful_request_number

    def _cache_path(self, variation_id: int) -> Path:
        return self._cache_directory / f"{variation_id}.json"

    def _load_cached(self, variation_id: int) -> ClinVarSummary | None:
        if self._refresh:
            return None

        path = self._cache_path(variation_id)

        if not path.exists():
            return None

        raw = json.loads(path.read_text(encoding="utf-8"))
        summary = raw.get("raw_summary")

        if summary is not None and not isinstance(summary, dict):
            raise ValueError(f"Invalid ClinVar cache entry: {path}")

        return ClinVarSummary(
            variation_id=variation_id,
            retrieved_at=str(raw["retrieved_at"]),
            request_url=str(raw["request_url"]),
            cached=True,
            raw_summary=summary,
        )

    def _save_cached(self, summary: ClinVarSummary) -> None:
        write_json(
            self._cache_path(summary.variation_id),
            {
                "format_version": 1,
                "provider": "ncbi_clinvar_esummary",
                "variation_id": summary.variation_id,
                "retrieved_at": summary.retrieved_at,
                "request_url": summary.request_url,
                "raw_summary_sha256": (
                    sha256_json(summary.raw_summary)
                    if summary.raw_summary is not None
                    else None
                ),
                "raw_summary": summary.raw_summary,
            },
        )

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = self._minimum_interval_seconds - elapsed

        if remaining > 0:
            time.sleep(remaining)

    def _request_batch(self, variation_ids: Sequence[int]) -> dict[str, object]:
        public_parameters = {
            "db": "clinvar",
            "id": ",".join(str(identifier) for identifier in variation_ids),
            "retmode": "json",
            "tool": "variantgate",
        }

        if self._email:
            public_parameters["email"] = self._email

        request_parameters = dict(public_parameters)

        if self._api_key:
            request_parameters["api_key"] = self._api_key

        request_url = (
            EUTILS_SUMMARY_URL
            + "?"
            + urllib.parse.urlencode(request_parameters)
        )
        public_url = (
            EUTILS_SUMMARY_URL
            + "?"
            + urllib.parse.urlencode(public_parameters)
        )
        request = urllib.request.Request(
            request_url,
            headers={"User-Agent": "VariantGate/0.3"},
        )

        for attempt in range(self._retry_number + 1):
            self._wait_for_rate_limit()
            self._request_attempt_number += 1

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self._last_request_time = time.monotonic()
                self._successful_request_number += 1
                return {
                    "public_url": public_url,
                    "payload": payload,
                }
            except urllib.error.HTTPError as error:
                self._last_request_time = time.monotonic()

                if error.code != 429 and error.code < 500:
                    raise

                if attempt == self._retry_number:
                    raise
            except urllib.error.URLError:
                self._last_request_time = time.monotonic()

                if attempt == self._retry_number:
                    raise

            time.sleep(min(2**attempt, 30))

        raise RuntimeError("ClinVar request retry loop ended unexpectedly")

    def fetch_many(
        self,
        variation_ids: Sequence[int],
    ) -> dict[int, ClinVarSummary]:
        ordered_ids = list(dict.fromkeys(int(value) for value in variation_ids))

        if any(identifier <= 0 for identifier in ordered_ids):
            raise ValueError("ClinVar Variation IDs must be positive")

        summaries: dict[int, ClinVarSummary] = {}
        missing_ids: list[int] = []

        for variation_id in ordered_ids:
            cached = self._load_cached(variation_id)

            if cached is None:
                missing_ids.append(variation_id)
            else:
                summaries[variation_id] = cached

        for batch in chunks(missing_ids, self._batch_size):
            response = self._request_batch(batch)
            payload = response["payload"]
            result = payload.get("result")

            if not isinstance(result, dict):
                raise ValueError("ClinVar ESummary returned no result object")

            retrieved_at = utc_now()

            for variation_id in batch:
                raw_summary = result.get(str(variation_id))

                if raw_summary is not None and not isinstance(
                    raw_summary,
                    dict,
                ):
                    raise ValueError(
                        "ClinVar ESummary returned an invalid summary for "
                        f"Variation ID {variation_id}"
                    )

                summary = ClinVarSummary(
                    variation_id=variation_id,
                    retrieved_at=retrieved_at,
                    request_url=str(response["public_url"]),
                    cached=False,
                    raw_summary=raw_summary,
                )
                self._save_cached(summary)
                summaries[variation_id] = summary

        return summaries


def normalize_summary(summary: ClinVarSummary) -> dict[str, object]:
    if summary.raw_summary is None:
        return {
            "status": "not_found",
            "variation_id": summary.variation_id,
        }

    raw = summary.raw_summary
    germline = raw.get("germline_classification") or {}
    variation_set = raw.get("variation_set") or []
    canonical_spdi: list[str] = []
    database_cross_references: list[dict[str, str]] = []

    for variation in variation_set:
        spdi = variation.get("canonical_spdi")

        if spdi and spdi not in canonical_spdi:
            canonical_spdi.append(str(spdi))

        for cross_reference in variation.get("variation_xrefs") or []:
            database_cross_references.append(
                {
                    "database": str(cross_reference.get("db_source", "")),
                    "identifier": str(cross_reference.get("db_id", "")),
                }
            )

    traits = [
        {
            "name": str(trait.get("trait_name", "")),
            "cross_references": [
                {
                    "database": str(reference.get("db_source", "")),
                    "identifier": str(reference.get("db_id", "")),
                }
                for reference in trait.get("trait_xrefs") or []
            ],
        }
        for trait in germline.get("trait_set") or []
    ]
    supporting_submissions = raw.get("supporting_submissions") or {}

    return {
        "status": "found",
        "variation_id": summary.variation_id,
        "accession": raw.get("accession"),
        "accession_version": raw.get("accession_version"),
        "title": raw.get("title"),
        "object_type": raw.get("obj_type"),
        "germline_classification": {
            "description": germline.get("description"),
            "review_status": germline.get("review_status"),
            "last_evaluated": germline.get("last_evaluated"),
            "traits": traits,
        },
        "genes": [
            {
                "symbol": gene.get("symbol"),
                "gene_id": gene.get("geneid"),
                "source": gene.get("source"),
            }
            for gene in raw.get("genes") or []
        ],
        "molecular_consequences": raw.get("molecular_consequence_list") or [],
        "protein_change": raw.get("protein_change"),
        "canonical_spdi": canonical_spdi,
        "database_cross_references": database_cross_references,
        "supporting_submission_counts": {
            "scv": len(supporting_submissions.get("scv") or []),
            "rcv": len(supporting_submissions.get("rcv") or []),
        },
        "clinvar_url": (
            "https://www.ncbi.nlm.nih.gov/clinvar/variation/"
            f"{summary.variation_id}/"
        ),
    }
