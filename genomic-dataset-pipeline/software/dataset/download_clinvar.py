from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import requests


DEFAULT_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz"


def md5sum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, force: bool = False) -> Path:
    if destination.exists() and not force:
        print(f"Using existing file: {destination}")
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    with requests.get(url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        received = 0
        with partial.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                received += len(chunk)
                if total:
                    print(f"\r{received / total:6.1%}", end="", flush=True)
    if total:
        print()
    partial.replace(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the current ClinVar GRCh38 VCF.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=Path("data/raw/clinvar_grch38.vcf.gz"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    path = download(args.url, args.output, args.force)
    checksum = md5sum(path)
    path.with_suffix(path.suffix + ".md5.local").write_text(checksum + "\n", encoding="utf-8")
    print(f"Saved {path} ({path.stat().st_size:,} bytes; md5={checksum})")


if __name__ == "__main__":
    main()

