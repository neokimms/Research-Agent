from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable

from .citations import arxiv_url, doi_url, extract_arxiv_id, normalize_doi, normalize_source_record, prefer_source, source_identity_key
from .config import SourceSettings
from .gemini_client import GeminiError, GeminiGenerateClient, gemini_output_text
from .models import RunWarning, SourceRecord
from .openai_client import OpenAIError, OpenAIResponsesClient, output_text
from .retry import RetryConfig, retry_call


USER_AGENT = "obsidian-research-agent/0.1"
DEFAULT_OFFICIAL_DOC_LIMIT = 6
logger = logging.getLogger(__name__)


def seed_official_sources(topic: str, source_settings: SourceSettings, *, limit: int = 4) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for domain in source_settings.official_doc_domains[:limit]:
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=f"Official documentation candidate for {topic}",
                    url=f"https://{domain}/",
                    source_type="official-docs",
                    summary="Seed official documentation source. Fetch or search this domain for exact evidence.",
                ),
                provider="seed",
            )
        )
    return records


def collect_official_doc_sources(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str | None,
    model: str,
    provider: str = "openai",
    limit: int = DEFAULT_OFFICIAL_DOC_LIMIT,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.official_doc_domains if domain.strip()]
    if not api_key or not domains:
        return seed_official_sources(topic, source_settings)

    if provider == "gemini":
        return _collect_official_docs_with_gemini(topic, source_settings, api_key=api_key, model=model, limit=limit)

    return _collect_official_docs_with_openai(topic, source_settings, api_key=api_key, model=model, limit=limit)


def collect_standard_sources(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str | None,
    model: str,
    provider: str = "openai",
    limit: int = 3,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.standards_domains if domain.strip()]
    if not api_key or not domains:
        return seed_standard_sources(topic, source_settings, limit=limit)

    if provider == "gemini":
        return _collect_standards_with_gemini(topic, source_settings, api_key=api_key, model=model, limit=limit)

    return _collect_standards_with_openai(topic, source_settings, api_key=api_key, model=model, limit=limit)


def _collect_official_docs_with_openai(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str,
    model: str,
    limit: int,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.official_doc_domains if domain.strip()]

    client = OpenAIResponsesClient(api_key=api_key, default_model=model, timeout_seconds=60)
    prompt = _official_docs_prompt(topic, domains, limit)
    try:
        response = client.create(
            input_text=prompt,
            instructions=(
                "You find official technical documentation pages. "
                "Return only a JSON array of objects with title, url, summary, and domain. "
                "Do not include unofficial websites, blogs, ads, or generic home pages unless no better official page exists."
            ),
            model=model,
            tools=[
                {
                    "type": "web_search",
                    "search_context_size": "low",
                    "filters": {"allowed_domains": domains},
                }
            ],
            tool_choice="required",
        )
    except OpenAIError as exc:
        logger.warning(
            "official docs collection failed; using seed sources",
            extra={"stage": "collect_official_docs", "provider": "openai", "topic": topic, "error": str(exc)},
        )
        return seed_official_sources(topic, source_settings)

    records = _records_from_official_response(
        response,
        text=output_text(response),
        allowed_domains=domains,
        source_provider="openai-web-search",
        limit=limit,
    )
    if records:
        return records
    logger.warning(
        "official docs collection returned no usable URLs; using seed sources",
        extra={"stage": "collect_official_docs", "provider": "openai", "topic": topic},
    )
    return seed_official_sources(topic, source_settings)


def _collect_standards_with_openai(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str,
    model: str,
    limit: int,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.standards_domains if domain.strip()]

    client = OpenAIResponsesClient(api_key=api_key, default_model=model, timeout_seconds=60)
    prompt = _standards_prompt(topic, domains, limit)
    try:
        response = client.create(
            input_text=prompt,
            instructions=(
                "You find official standards, governance, and security framework pages. "
                "Return only a JSON array of objects with title, url, summary, and domain. "
                "Do not include unofficial websites, blogs, ads, or generic home pages unless no better official page exists."
            ),
            model=model,
            tools=[
                {
                    "type": "web_search",
                    "search_context_size": "low",
                    "filters": {"allowed_domains": domains},
                }
            ],
            tool_choice="required",
        )
    except OpenAIError as exc:
        logger.warning(
            "standards collection failed; using seed sources",
            extra={"stage": "collect_standards", "provider": "openai", "topic": topic, "error": str(exc)},
        )
        return seed_standard_sources(topic, source_settings, limit=limit)

    records = _records_from_official_response(
        response,
        text=output_text(response),
        allowed_domains=domains,
        source_provider="openai-web-search",
        source_type="standards",
        limit=limit,
    )
    if records:
        return records
    logger.warning(
        "standards collection returned no usable URLs; using seed sources",
        extra={"stage": "collect_standards", "provider": "openai", "topic": topic},
    )
    return seed_standard_sources(topic, source_settings, limit=limit)


def _collect_official_docs_with_gemini(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str,
    model: str,
    limit: int,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.official_doc_domains if domain.strip()]
    client = GeminiGenerateClient(api_key=api_key, default_model=model, timeout_seconds=60)
    prompt = _official_docs_prompt(topic, domains, limit)
    try:
        response = client.generate(
            input_text=prompt,
            instructions=(
                "You find official technical documentation pages. "
                "Use Google Search if needed. Return JSON only. "
                "The JSON must include title, url, summary, and domain."
            ),
            model=model,
            tools=[{"google_search": {}}],
        )
    except GeminiError as exc:
        logger.warning(
            "official docs collection failed; using seed sources",
            extra={"stage": "collect_official_docs", "provider": "gemini", "topic": topic, "error": str(exc)},
        )
        return seed_official_sources(topic, source_settings)

    records = _records_from_official_response(
        response,
        text=gemini_output_text(response),
        allowed_domains=domains,
        source_provider="gemini-google-search",
        limit=limit,
    )
    if records:
        return records
    logger.warning(
        "official docs collection returned no usable URLs; using seed sources",
        extra={"stage": "collect_official_docs", "provider": "gemini", "topic": topic},
    )
    return seed_official_sources(topic, source_settings)


def _collect_standards_with_gemini(
    topic: str,
    source_settings: SourceSettings,
    *,
    api_key: str,
    model: str,
    limit: int,
) -> list[SourceRecord]:
    domains = [domain.strip().lower() for domain in source_settings.standards_domains if domain.strip()]
    client = GeminiGenerateClient(api_key=api_key, default_model=model, timeout_seconds=60)
    prompt = _standards_prompt(topic, domains, limit)
    try:
        response = client.generate(
            input_text=prompt,
            instructions=(
                "You find official standards, governance, and security framework pages. "
                "Use Google Search if needed. Return JSON only. "
                "The JSON must include title, url, summary, and domain."
            ),
            model=model,
            tools=[{"google_search": {}}],
        )
    except GeminiError as exc:
        logger.warning(
            "standards collection failed; using seed sources",
            extra={"stage": "collect_standards", "provider": "gemini", "topic": topic, "error": str(exc)},
        )
        return seed_standard_sources(topic, source_settings, limit=limit)

    records = _records_from_official_response(
        response,
        text=gemini_output_text(response),
        allowed_domains=domains,
        source_provider="gemini-google-search",
        source_type="standards",
        limit=limit,
    )
    if records:
        return records
    logger.warning(
        "standards collection returned no usable URLs; using seed sources",
        extra={"stage": "collect_standards", "provider": "gemini", "topic": topic},
    )
    return seed_standard_sources(topic, source_settings, limit=limit)


def seed_standard_sources(topic: str, source_settings: SourceSettings, *, limit: int = 3) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for domain in source_settings.standards_domains[:limit]:
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=f"Standards or security framework candidate for {topic}",
                    url=f"https://{domain}/",
                    source_type="standards",
                    summary="Seed standards source. Use when the topic touches governance, security, risk, or compliance.",
                ),
                provider="seed",
            )
        )
    return records


def _official_docs_prompt(topic: str, domains: list[str], limit: int) -> str:
    domain_lines = "\n".join(f"- {domain}" for domain in domains)
    return f"""Topic:
{topic}

Allowed official documentation domains:
{domain_lines}

Find up to {limit} high-signal official documentation pages relevant to the topic.
Prefer exact guide, API reference, concept, or framework overview pages over home pages.

Return JSON only:
[
  {{
    "title": "Exact page title",
    "url": "https://official.domain/path",
    "summary": "One sentence explaining why this page is relevant.",
    "domain": "official.domain"
  }}
]
"""


def _standards_prompt(topic: str, domains: list[str], limit: int) -> str:
    domain_lines = "\n".join(f"- {domain}" for domain in domains)
    return f"""Topic:
{topic}

Allowed official standards and security framework domains:
{domain_lines}

Find up to {limit} high-signal official standards, governance, risk, compliance, or security framework pages relevant to the topic.
Prefer exact standard, publication, project, or framework overview pages over home pages.

Return JSON only:
[
  {{
    "title": "Exact page title",
    "url": "https://official.domain/path",
    "summary": "One sentence explaining why this page is relevant.",
    "domain": "official.domain"
  }}
]
"""


def _records_from_official_response(
    response: dict,
    *,
    text: str,
    allowed_domains: list[str],
    source_provider: str,
    limit: int,
    source_type: str = "official-docs",
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for item in _json_array_items(text):
        record = _record_from_mapping(item, allowed_domains=allowed_domains, source_provider=source_provider, source_type=source_type)
        if record:
            records.append(record)

    if not records:
        for item in _source_mappings(response):
            record = _record_from_mapping(item, allowed_domains=allowed_domains, source_provider=source_provider, source_type=source_type)
            if record:
                records.append(record)

    return deduplicate_sources(records)[:limit]


def _json_array_items(text: str) -> list[dict]:
    if not text.strip():
        return []
    candidates = [text.strip()]
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            results = value.get("results") or value.get("sources")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
    return []


def _source_mappings(value: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            found.append(value)
        source_list = value.get("sources")
        if isinstance(source_list, list):
            found.extend(item for item in source_list if isinstance(item, dict))
        url_citation = value.get("url_citation")
        if isinstance(url_citation, dict):
            found.append(url_citation)
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_source_mappings(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_source_mappings(item))
    return found


def _record_from_mapping(item: dict, *, allowed_domains: list[str], source_provider: str, source_type: str = "official-docs") -> SourceRecord | None:
    url = str(item.get("url") or item.get("link") or "").strip()
    if not url or not _url_in_allowed_domains(url, allowed_domains):
        return None
    title = str(item.get("title") or item.get("name") or url).strip()
    summary = str(item.get("summary") or item.get("snippet") or item.get("description") or "").strip()
    domain = urllib.parse.urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return normalize_source_record(
        SourceRecord(
            title=title,
            url=url,
            source_type=source_type,
            summary=summary or f"Official documentation page from {domain}.",
        ),
        provider=source_provider,
    )


def _url_in_allowed_domains(url: str, allowed_domains: list[str]) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    try:
        parsed.port
    except ValueError:
        return False
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return any(netloc == domain or netloc.endswith(f".{domain}") for domain in allowed_domains)


def search_arxiv(topic: str, *, limit: int = 5, timeout_seconds: int = 20) -> list[SourceRecord]:
    params = urllib.parse.urlencode({
        "search_query": f'all:"{topic}"',
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    text = _get_text(url, timeout_seconds=timeout_seconds)
    root = ET.fromstring(text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    records: list[SourceRecord] = []
    for entry in root.findall("atom:entry", ns):
        title = _xml_text(entry.find("atom:title", ns))
        page_url = _xml_text(entry.find("atom:id", ns))
        summary = _xml_text(entry.find("atom:summary", ns))
        published = _xml_text(entry.find("atom:published", ns))
        updated = _xml_text(entry.find("atom:updated", ns))
        authors = [_xml_text(author.find("atom:name", ns)) for author in entry.findall("atom:author", ns)]
        arxiv_id = extract_arxiv_id(page_url)
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=title,
                    url=page_url,
                    source_type="papers",
                    summary=summary,
                    authors=[author for author in authors if author],
                    published_at=published,
                    updated_at=updated,
                    arxiv_id=arxiv_id,
                    canonical_url=arxiv_url(arxiv_id),
                ),
                provider="arxiv",
            )
        )
    return records


def search_crossref(topic: str, *, limit: int = 5, timeout_seconds: int = 20) -> list[SourceRecord]:
    params = urllib.parse.urlencode({"query.bibliographic": topic, "rows": limit})
    url = f"https://api.crossref.org/works?{params}"
    data = json.loads(_get_text(url, timeout_seconds=timeout_seconds))
    items = data.get("message", {}).get("items", [])
    records: list[SourceRecord] = []
    for item in items:
        title = _first(item.get("title")) or "Untitled Crossref work"
        doi = normalize_doi(str(item.get("DOI", "")))
        page_url = item.get("URL") or doi_url(doi)
        authors = [
            " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part)
            for author in item.get("author", [])
            if isinstance(author, dict)
        ]
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=title,
                    url=page_url,
                    source_type="papers",
                    summary=item.get("abstract", "") or "Crossref metadata record.",
                    authors=authors,
                    published_at=_date_parts(item.get("published-print") or item.get("published-online")),
                    doi=doi,
                    canonical_url=doi_url(doi),
                ),
                provider="crossref",
            )
        )
    return records


def search_openalex(topic: str, *, limit: int = 5, timeout_seconds: int = 20) -> list[SourceRecord]:
    params = urllib.parse.urlencode({"search": topic, "per-page": limit})
    url = f"https://api.openalex.org/works?{params}"
    data = json.loads(_get_text(url, timeout_seconds=timeout_seconds))
    records: list[SourceRecord] = []
    for item in data.get("results", []):
        title = item.get("display_name") or "Untitled OpenAlex work"
        doi = normalize_doi(str(item.get("doi", "")))
        page_url = doi_url(doi) or item.get("id") or ""
        authors = []
        for authorship in item.get("authorships", []):
            author = authorship.get("author", {}) if isinstance(authorship, dict) else {}
            name = author.get("display_name")
            if name:
                authors.append(name)
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=title,
                    url=page_url,
                    source_type="papers",
                    summary="OpenAlex metadata record.",
                    authors=authors,
                    published_at=str(item.get("publication_date", "")),
                    doi=doi,
                    canonical_url=doi_url(doi),
                ),
                provider="openalex",
            )
        )
    return records


def search_semantic_scholar(topic: str, *, limit: int = 5, timeout_seconds: int = 20) -> list[SourceRecord]:
    fields = ",".join(
        [
            "title",
            "url",
            "abstract",
            "authors",
            "year",
            "publicationDate",
            "externalIds",
        ]
    )
    params = urllib.parse.urlencode({"query": topic, "limit": limit, "fields": fields})
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{params}"
    data = json.loads(_get_text(url, timeout_seconds=timeout_seconds))
    records: list[SourceRecord] = []
    for item in data.get("data", []):
        if not isinstance(item, dict):
            continue
        external_ids = item.get("externalIds", {}) if isinstance(item.get("externalIds"), dict) else {}
        doi = normalize_doi(str(external_ids.get("DOI", "")))
        arxiv_id = str(external_ids.get("ArXiv", "")).strip()
        page_url = item.get("url") or doi_url(doi) or arxiv_url(arxiv_id)
        authors = [
            str(author.get("name", "")).strip()
            for author in item.get("authors", [])
            if isinstance(author, dict) and str(author.get("name", "")).strip()
        ]
        published_at = str(item.get("publicationDate") or item.get("year") or "")
        records.append(
            normalize_source_record(
                SourceRecord(
                    title=str(item.get("title") or "Untitled Semantic Scholar paper"),
                    url=str(page_url or ""),
                    source_type="papers",
                    summary=str(item.get("abstract") or "Semantic Scholar metadata record."),
                    authors=authors,
                    published_at=published_at,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    canonical_url=doi_url(doi) or arxiv_url(arxiv_id),
                ),
                provider="semantic-scholar",
            )
        )
    return records


def collect_paper_sources(
    topic: str,
    enabled_sources: Iterable[str],
    *,
    limit_each: int = 3,
    warnings: list[RunWarning] | None = None,
) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for source in enabled_sources:
        try:
            if source == "arxiv":
                records.extend(search_arxiv(topic, limit=limit_each))
            elif source == "semantic-scholar":
                records.extend(search_semantic_scholar(topic, limit=limit_each))
            elif source == "crossref":
                records.extend(search_crossref(topic, limit=limit_each))
            elif source == "openalex":
                records.extend(search_openalex(topic, limit=limit_each))
            else:
                logger.warning(
                    "unknown paper source configured",
                    extra={"stage": "collect_papers", "source": source, "topic": topic},
                )
                _append_warning(warnings, source=source, detail="unknown paper source configured")
        except Exception as exc:
            logger.warning(
                "paper collector failed",
                extra={
                    "stage": "collect_papers",
                    "source": source,
                    "topic": topic,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            _append_warning(warnings, source=source, detail=f"{type(exc).__name__}: {exc}")
    return deduplicate_sources(records)


def _append_warning(warnings: list[RunWarning] | None, *, source: str, detail: str) -> None:
    if warnings is not None:
        warnings.append(RunWarning(category="paper collector", source=source, detail=detail))


def deduplicate_sources(records: list[SourceRecord]) -> list[SourceRecord]:
    by_key: dict[str, SourceRecord] = {}
    order: list[str] = []
    for record in records:
        normalized = normalize_source_record(record)
        key = source_identity_key(normalized)
        if not key:
            continue
        if key not in by_key:
            by_key[key] = normalized
            order.append(key)
        else:
            by_key[key] = prefer_source(normalized, by_key[key])
    return [by_key[key] for key in order]


def _get_text(url: str, *, timeout_seconds: int) -> str:
    def fetch() -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8", errors="replace")

    return retry_call(
        fetch,
        label="source metadata request",
        logger=logger,
        config=RetryConfig(attempts=3, initial_delay_seconds=0.5),
    )


def _xml_text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _first(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _date_parts(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    date_parts = value.get("date-parts")
    if not isinstance(date_parts, list) or not date_parts:
        return ""
    first = date_parts[0]
    if not isinstance(first, list):
        return ""
    return "-".join(str(part) for part in first)
