from __future__ import annotations

import re
import urllib.parse
from dataclasses import replace

from .models import SourceRecord


DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:(?:arxiv:)?)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)", re.IGNORECASE)


def normalize_source_record(record: SourceRecord, *, provider: str = "") -> SourceRecord:
    doi = normalize_doi(record.doi) or normalize_doi(record.url) or normalize_doi(record.canonical_url)
    arxiv_id = normalize_arxiv_id(record.arxiv_id) or extract_arxiv_id(record.url) or extract_arxiv_id(record.canonical_url)
    canonical_url = record.canonical_url.strip() or canonical_url_for(doi=doi, arxiv_id=arxiv_id, url=record.url)
    source_provider = record.source_provider.strip() or provider
    normalized = replace(
        record,
        doi=doi,
        arxiv_id=arxiv_id,
        source_provider=source_provider,
        canonical_url=canonical_url,
    )
    if normalized.source_score <= 0:
        normalized = replace(normalized, source_score=score_source(normalized))
    return normalized


def source_identity_key(record: SourceRecord) -> str:
    normalized = normalize_source_record(record)
    if normalized.doi:
        return f"doi:{normalized.doi}"
    if normalized.arxiv_id:
        return f"arxiv:{normalized.arxiv_id.lower()}"
    if normalized.canonical_url:
        return f"url:{normalized.canonical_url.lower()}"
    if normalized.url:
        return f"url:{canonicalize_url(normalized.url).lower()}"
    title = _compact_title(normalized.title)
    return f"title:{title}" if title else ""


def prefer_source(candidate: SourceRecord, current: SourceRecord) -> SourceRecord:
    candidate_score = _quality_tuple(candidate)
    current_score = _quality_tuple(current)
    return candidate if candidate_score > current_score else current


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = urllib.parse.unquote(text)
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    match = DOI_RE.search(text)
    doi = match.group(1) if match else text if text.lower().startswith("10.") else ""
    return doi.rstrip(".,);]").lower()


def doi_url(doi: str) -> str:
    normalized = normalize_doi(doi)
    return f"https://doi.org/{normalized}" if normalized else ""


def normalize_arxiv_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.removeprefix("arXiv:").removeprefix("arxiv:")
    text = text.removesuffix(".pdf")
    match = ARXIV_RE.search(text)
    return match.group(1) if match else ""


def extract_arxiv_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    path = parsed.path.strip("/")
    if parsed.netloc.lower().endswith("arxiv.org"):
        for prefix in ["abs/", "pdf/"]:
            if path.startswith(prefix):
                return normalize_arxiv_id(path[len(prefix):])
    if parsed.scheme or parsed.netloc:
        return ""
    return normalize_arxiv_id(text)


def arxiv_url(arxiv_id: str) -> str:
    normalized = normalize_arxiv_id(arxiv_id)
    return f"https://arxiv.org/abs/{normalized}" if normalized else ""


def canonical_url_for(*, doi: str = "", arxiv_id: str = "", url: str = "") -> str:
    if doi:
        return doi_url(doi)
    if arxiv_id:
        return arxiv_url(arxiv_id)
    return canonicalize_url(url)


def canonicalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    doi = normalize_doi(text)
    if doi:
        return doi_url(doi)
    arxiv_id = extract_arxiv_id(text)
    if arxiv_id:
        return arxiv_url(arxiv_id)

    parsed = urllib.parse.urlparse(text)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = parsed.query
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def score_source(record: SourceRecord) -> float:
    if record.source_type == "run-log":
        return 0.0
    if record.doi:
        return 0.95
    if record.arxiv_id:
        return 0.9
    if record.source_type == "official-docs":
        if not record.url and not record.canonical_url:
            return 0.35
        parsed = urllib.parse.urlparse(record.url or record.canonical_url)
        return 0.6 if parsed.path in {"", "/"} else 0.88
    if record.source_type == "standards":
        return 0.75 if record.url else 0.45
    if record.source_type == "papers":
        return 0.7 if record.url or record.canonical_url else 0.35
    return 0.5 if record.url or record.canonical_url else 0.25


def _quality_tuple(record: SourceRecord) -> tuple[float, int, int, int, int, int]:
    return (
        record.source_score,
        int(bool(record.doi)),
        int(bool(record.arxiv_id)),
        int(bool(record.url or record.canonical_url)),
        int(bool(record.summary)),
        len(record.authors),
    )


def _compact_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())
