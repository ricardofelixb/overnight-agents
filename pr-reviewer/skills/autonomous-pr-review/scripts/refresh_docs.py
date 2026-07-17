#!/usr/bin/env python3
"""Fetch allowlisted official docs with a freshness-bounded cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch(url: str, allowed_hosts: set[str], timeout: int, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "autonomous-pr-review/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        final_host = urllib.parse.urlparse(final_url).hostname
        if final_host not in allowed_hosts:
            raise ValueError(f"redirected to non-allowlisted host: {final_host}")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"document exceeds {max_bytes} bytes")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"document exceeds {max_bytes} bytes")
        return body, final_url


def refresh_domain(
    name: str,
    config: dict[str, Any],
    cache_root: Path,
    max_age: timedelta,
    timeout: int,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    allowed_hosts = set(config["allowed_hosts"])
    domain_root = cache_root / name
    domain_root.mkdir(parents=True, exist_ok=True)

    for url in config["urls"]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
            errors.append(f"{name}: non-allowlisted URL {url}")
            continue
        key = hashlib.sha256(url.encode()).hexdigest()
        content_path = domain_root / f"{key}.content"
        metadata_path = domain_root / f"{key}.json"
        source = "network"
        final_url = url
        retrieved_at = now()
        try:
            body, final_url = fetch(url, allowed_hosts, timeout, max_bytes)
            atomic_write(content_path, body)
            metadata = {
                "url": url,
                "final_url": final_url,
                "retrieved_at": retrieved_at.isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            atomic_write(metadata_path, (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode())
        except (OSError, ValueError, urllib.error.URLError) as error:
            if not content_path.exists() or not metadata_path.exists():
                errors.append(f"{name}: {url}: {error}")
                continue
            metadata = json.loads(metadata_path.read_text())
            retrieved_at = parse_time(metadata["retrieved_at"])
            if now() - retrieved_at > max_age:
                errors.append(f"{name}: stale cache for {url}: {error}")
                continue
            final_url = metadata["final_url"]
            source = "cache"

        entries.append(
            {
                "domain": name,
                "url": url,
                "final_url": final_url,
                "retrieved_at": retrieved_at.isoformat(),
                "source": source,
                "content_path": str(content_path.resolve()),
                "sha256": hashlib.sha256(content_path.read_bytes()).hexdigest(),
            }
        )
    return entries, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-document-bytes", type=int, default=5_000_000)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text())
    available = catalog.get("domains", {})
    selected = sorted(set(args.domain))
    unknown = [name for name in selected if name not in available]
    if unknown:
        print(f"unknown documentation domains: {unknown}", file=sys.stderr)
        return 2

    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in selected:
        domain_entries, domain_errors = refresh_domain(
            name,
            available[name],
            args.cache_dir,
            timedelta(hours=args.max_age_hours),
            args.timeout_seconds,
            args.max_document_bytes,
        )
        entries.extend(domain_entries)
        errors.extend(domain_errors)

    manifest = {
        "version": 1,
        "created_at": now().isoformat(),
        "domains": selected,
        "documents": entries,
        "errors": errors,
    }
    atomic_write(args.manifest, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
