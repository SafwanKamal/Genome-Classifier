from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
)
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


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


def chunks(values: Sequence[object], size: int) -> list[list[object]]:
    return [
        list(values[start : start + size])
        for start in range(0, len(values), size)
    ]


def parse_elink_response(payload: dict[str, object]) -> dict[int, list[str]]:
    linksets = payload.get("linksets")

    if not isinstance(linksets, list):
        raise ValueError("NCBI ELink returned no linksets array")

    result: dict[int, list[str]] = {}

    for linkset in linksets:
        if not isinstance(linkset, dict):
            raise ValueError("NCBI ELink returned an invalid linkset")

        input_ids = linkset.get("ids") or []

        if len(input_ids) != 1:
            raise ValueError(
                "NCBI ELink did not preserve one-to-one input identifiers"
            )

        variation_id = int(input_ids[0])
        pmids: list[str] = []

        for link_database in linkset.get("linksetdbs") or []:
            if link_database.get("dbto") != "pubmed":
                continue

            for pmid in link_database.get("links") or []:
                pmid_text = str(pmid)

                if pmid_text not in pmids:
                    pmids.append(pmid_text)

        result[variation_id] = pmids

    return result


def normalize_pubmed_summary(
    pmid: str,
    raw_summary: dict[str, object] | None,
) -> dict[str, object]:
    if raw_summary is None:
        return {
            "status": "not_found",
            "pmid": pmid,
        }

    article_ids = raw_summary.get("articleids") or []

    def identifier(identifier_type: str) -> str | None:
        for article_id in article_ids:
            if article_id.get("idtype") == identifier_type:
                value = article_id.get("value")
                return str(value) if value else None

        return None

    authors = [
        str(author.get("name"))
        for author in raw_summary.get("authors") or []
        if author.get("name")
    ]

    return {
        "status": "found",
        "pmid": pmid,
        "title": raw_summary.get("title"),
        "authors": authors,
        "last_author": raw_summary.get("lastauthor"),
        "journal": raw_summary.get("fulljournalname") or raw_summary.get("source"),
        "publication_date": raw_summary.get("pubdate"),
        "electronic_publication_date": raw_summary.get("epubdate"),
        "publication_types": raw_summary.get("pubtype") or [],
        "doi": identifier("doi"),
        "pmc_id": identifier("pmc"),
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def parse_esearch_response(payload: dict[str, object]) -> dict[str, object]:
    result = payload.get("esearchresult")

    if not isinstance(result, dict):
        raise ValueError("PubMed ESearch returned no esearchresult object")

    identifiers = result.get("idlist")

    if not isinstance(identifiers, list):
        raise ValueError("PubMed ESearch returned no idlist array")

    return {
        "count": int(result.get("count", len(identifiers))),
        "pmids": list(dict.fromkeys(str(value) for value in identifiers)),
        "query_translation": result.get("querytranslation"),
    }


def parse_pubmed_abstracts(xml_payload: bytes) -> dict[str, dict[str, object]]:
    root = ET.fromstring(xml_payload)
    parsed: dict[str, dict[str, object]] = {}

    for article in root.findall(".//PubmedArticle"):
        pmid_node = article.find(".//MedlineCitation/PMID")

        if pmid_node is None or not (pmid_node.text or "").strip():
            continue

        pmid = str(pmid_node.text).strip()
        sections: list[dict[str, str | None]] = []

        for abstract_node in article.findall(
            ".//Article/Abstract/AbstractText"
        ):
            text_value = "".join(abstract_node.itertext()).strip()

            if not text_value:
                continue

            sections.append(
                {
                    "label": abstract_node.attrib.get("Label"),
                    "text": text_value,
                }
            )

        mesh_terms = [
            "".join(node.itertext()).strip()
            for node in article.findall(
                ".//MeshHeadingList/MeshHeading/DescriptorName"
            )
            if "".join(node.itertext()).strip()
        ]
        keywords = [
            "".join(node.itertext()).strip()
            for node in article.findall(".//KeywordList/Keyword")
            if "".join(node.itertext()).strip()
        ]
        parsed[pmid] = {
            "status": "found",
            "pmid": pmid,
            "abstract": "\n".join(
                (
                    f"{section['label']}: {section['text']}"
                    if section["label"]
                    else str(section["text"])
                )
                for section in sections
            ) or None,
            "abstract_sections": sections,
            "mesh_terms": list(dict.fromkeys(mesh_terms)),
            "keywords": list(dict.fromkeys(keywords)),
        }

    return parsed


class PubMedClient:
    def __init__(
        self,
        cache_directory: Path,
        email: str | None = None,
        api_key_environment: str = "NCBI_API_KEY",
        link_batch_size: int = 100,
        summary_batch_size: int = 200,
        timeout_seconds: float = 30.0,
        retry_number: int = 4,
        refresh: bool = False,
    ) -> None:
        if not 1 <= link_batch_size <= 200:
            raise ValueError("link_batch_size must be between 1 and 200")

        if not 1 <= summary_batch_size <= 200:
            raise ValueError("summary_batch_size must be between 1 and 200")

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        if retry_number < 0:
            raise ValueError("retry_number cannot be negative")

        self._cache_directory = cache_directory.resolve()
        self._email = email
        self._api_key = os.environ.get(api_key_environment)
        self._link_batch_size = link_batch_size
        self._summary_batch_size = summary_batch_size
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

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        remaining = self._minimum_interval_seconds - elapsed

        if remaining > 0:
            time.sleep(remaining)

    def _request_json(
        self,
        base_url: str,
        public_parameters: list[tuple[str, str]],
    ) -> tuple[str, dict[str, object]]:
        request_parameters = list(public_parameters)

        if self._api_key:
            request_parameters.append(("api_key", self._api_key))

        request_url = base_url + "?" + urllib.parse.urlencode(
            request_parameters
        )
        public_url = base_url + "?" + urllib.parse.urlencode(
            public_parameters
        )
        request = urllib.request.Request(
            request_url,
            headers={"User-Agent": "VariantGate/0.6"},
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
                return public_url, payload
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

        raise RuntimeError("NCBI request retry loop ended unexpectedly")

    def _request_bytes(
        self,
        base_url: str,
        public_parameters: list[tuple[str, str]],
    ) -> tuple[str, bytes]:
        request_parameters = list(public_parameters)

        if self._api_key:
            request_parameters.append(("api_key", self._api_key))

        request_url = base_url + "?" + urllib.parse.urlencode(
            request_parameters
        )
        public_url = base_url + "?" + urllib.parse.urlencode(
            public_parameters
        )
        request = urllib.request.Request(
            request_url,
            headers={"User-Agent": "VariantGate/0.6"},
        )

        for attempt in range(self._retry_number + 1):
            self._wait_for_rate_limit()
            self._request_attempt_number += 1

            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    payload = response.read()
                self._last_request_time = time.monotonic()
                self._successful_request_number += 1
                return public_url, payload
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

        raise RuntimeError("NCBI request retry loop ended unexpectedly")

    def _base_parameters(self) -> list[tuple[str, str]]:
        parameters = [("retmode", "json"), ("tool", "variantgate")]

        if self._email:
            parameters.append(("email", self._email))

        return parameters

    def _link_cache_path(self, variation_id: int) -> Path:
        return self._cache_directory / "links" / f"{variation_id}.json"

    def _article_cache_path(self, pmid: str) -> Path:
        return self._cache_directory / "articles" / f"{pmid}.json"

    def _search_cache_path(self, query: str, retmax: int) -> Path:
        identity = sha256_json({"query": query, "retmax": retmax})
        return self._cache_directory / "searches" / f"{identity}.json"

    def _abstract_cache_path(self, pmid: str) -> Path:
        return self._cache_directory / "abstracts" / f"{pmid}.json"

    def search(
        self,
        query: str,
        retmax: int = 20,
    ) -> dict[str, object]:
        query = query.strip()

        if not query:
            raise ValueError("PubMed search query cannot be empty")

        if not 1 <= retmax <= 200:
            raise ValueError("retmax must be between 1 and 200")

        cache_path = self._search_cache_path(query, retmax)

        if cache_path.exists() and not self._refresh:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["cache_hit"] = True
            return cached

        parameters = [
            ("db", "pubmed"),
            ("term", query),
            ("retmax", str(retmax)),
            ("sort", "relevance"),
            *self._base_parameters(),
        ]
        request_url, payload = self._request_json(ESEARCH_URL, parameters)
        parsed = parse_esearch_response(payload)
        entry = {
            "format_version": 1,
            "provider": "ncbi_pubmed_esearch",
            "query": query,
            "retmax": retmax,
            "retrieved_at": utc_now(),
            "request_url": request_url,
            "raw_response_sha256": sha256_json(payload),
            "cache_hit": False,
            **parsed,
        }
        write_json(cache_path, entry)
        return entry

    def fetch_links(
        self,
        variation_ids: Sequence[int],
    ) -> dict[int, dict[str, object]]:
        ordered_ids = list(dict.fromkeys(int(value) for value in variation_ids))
        results: dict[int, dict[str, object]] = {}
        missing_ids: list[int] = []

        for variation_id in ordered_ids:
            path = self._link_cache_path(variation_id)

            if path.exists() and not self._refresh:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["cache_hit"] = True
                results[variation_id] = cached
            else:
                missing_ids.append(variation_id)

        for batch_values in chunks(missing_ids, self._link_batch_size):
            batch = [int(value) for value in batch_values]
            parameters = [
                ("dbfrom", "clinvar"),
                ("db", "pubmed"),
                *self._base_parameters(),
                *(("id", str(value)) for value in batch),
            ]
            request_url, payload = self._request_json(ELINK_URL, parameters)
            parsed = parse_elink_response(payload)
            retrieved_at = utc_now()

            for variation_id in batch:
                entry = {
                    "format_version": 1,
                    "provider": "ncbi_elink_clinvar_pubmed",
                    "variation_id": variation_id,
                    "pmids": parsed.get(variation_id, []),
                    "retrieved_at": retrieved_at,
                    "request_url": request_url,
                    "raw_response_sha256": sha256_json(payload),
                    "cache_hit": False,
                }
                write_json(self._link_cache_path(variation_id), entry)
                results[variation_id] = entry

        return results

    def fetch_articles(
        self,
        pmids: Sequence[str],
    ) -> dict[str, dict[str, object]]:
        ordered_pmids = list(dict.fromkeys(str(value) for value in pmids))
        results: dict[str, dict[str, object]] = {}
        missing_pmids: list[str] = []

        for pmid in ordered_pmids:
            path = self._article_cache_path(pmid)

            if path.exists() and not self._refresh:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["cache_hit"] = True
                results[pmid] = cached
            else:
                missing_pmids.append(pmid)

        for batch_values in chunks(missing_pmids, self._summary_batch_size):
            batch = [str(value) for value in batch_values]
            parameters = [
                ("db", "pubmed"),
                ("id", ",".join(batch)),
                *self._base_parameters(),
            ]
            request_url, payload = self._request_json(ESUMMARY_URL, parameters)
            result = payload.get("result")

            if not isinstance(result, dict):
                raise ValueError("PubMed ESummary returned no result object")

            retrieved_at = utc_now()

            for pmid in batch:
                raw_summary = result.get(pmid)

                if raw_summary is not None and not isinstance(raw_summary, dict):
                    raise ValueError(
                        f"PubMed ESummary returned an invalid PMID {pmid}"
                    )

                entry = {
                    "format_version": 1,
                    "provider": "ncbi_pubmed_esummary",
                    "pmid": pmid,
                    "retrieved_at": retrieved_at,
                    "request_url": request_url,
                    "raw_summary_sha256": (
                        sha256_json(raw_summary)
                        if raw_summary is not None
                        else None
                    ),
                    "cache_hit": False,
                    "article": normalize_pubmed_summary(pmid, raw_summary),
                }
                write_json(self._article_cache_path(pmid), entry)
                results[pmid] = entry

        return results

    def fetch_abstracts(
        self,
        pmids: Sequence[str],
    ) -> dict[str, dict[str, object]]:
        ordered_pmids = list(dict.fromkeys(str(value) for value in pmids))
        results: dict[str, dict[str, object]] = {}
        missing_pmids: list[str] = []

        for pmid in ordered_pmids:
            path = self._abstract_cache_path(pmid)

            if path.exists() and not self._refresh:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["cache_hit"] = True
                results[pmid] = cached
            else:
                missing_pmids.append(pmid)

        for batch_values in chunks(missing_pmids, self._summary_batch_size):
            batch = [str(value) for value in batch_values]
            parameters = [
                ("db", "pubmed"),
                ("id", ",".join(batch)),
                ("retmode", "xml"),
                *[
                    parameter
                    for parameter in self._base_parameters()
                    if parameter[0] != "retmode"
                ],
            ]
            request_url, payload = self._request_bytes(EFETCH_URL, parameters)
            parsed = parse_pubmed_abstracts(payload)
            retrieved_at = utc_now()

            for pmid in batch:
                abstract = parsed.get(
                    pmid,
                    {
                        "status": "not_found",
                        "pmid": pmid,
                        "abstract": None,
                        "abstract_sections": [],
                        "mesh_terms": [],
                        "keywords": [],
                    },
                )
                entry = {
                    "format_version": 1,
                    "provider": "ncbi_pubmed_efetch",
                    "pmid": pmid,
                    "retrieved_at": retrieved_at,
                    "request_url": request_url,
                    "raw_record_sha256": sha256_json(abstract),
                    "cache_hit": False,
                    "content": abstract,
                }
                write_json(self._abstract_cache_path(pmid), entry)
                results[pmid] = entry

        return results
