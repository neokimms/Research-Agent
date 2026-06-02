from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .citations import normalize_doi
from .collectors import _get_text
from .config import Settings
from .obsidian import ObsidianWriter
from .render import _translate_to_korean
from .source_reference_sync import SourceClaimSnapshot, SourceSnapshot, _source_snapshots
from .textutil import yaml_scalar
from .timeutil import now_local
from .vault_index import _frontmatter_scalar, _markdown_files, _set_frontmatter_scalars, _split_frontmatter


CLAIM_REFRESH_CHECK_RE = re.compile(r"^- \[(?P<state>[ xX])\]\s+(?P<proposal_id>C\d{3})\b", re.MULTILINE)
CANDIDATE_BLOCK_RE = re.compile(r"## Candidate Records\s*```json\s*(?P<json>[\s\S]*?)\s*```", re.MULTILINE)
GENERIC_METADATA_RE = re.compile(r"^(?:crossref|openalex|semantic scholar|arxiv)?\s*metadata record\.?$", re.IGNORECASE)


@dataclass(frozen=True)
class PaperSourceMetadata:
    doi: str
    arxiv_id: str
    authors: list[str]
    published_at: str
    provider: str
    venue: str = ""
    work_type: str = ""


@dataclass(frozen=True)
class PaperClaimRefreshCandidate:
    proposal_id: str
    source: SourceSnapshot
    claim: SourceClaimSnapshot
    metadata: PaperSourceMetadata
    new_summary: str
    new_claim: str
    new_evidence: str
    refresh_provider: str


@dataclass(frozen=True)
class PaperClaimRefreshResult:
    vault_path: Path
    paper_sources: int
    candidates: list[PaperClaimRefreshCandidate]
    warnings: list[str]


@dataclass(frozen=True)
class PaperClaimRefreshWriteResult:
    result: PaperClaimRefreshResult
    note_path: Path


@dataclass(frozen=True)
class PaperClaimRefreshApplyItem:
    proposal_path: Path
    proposal_id: str
    source_id: str
    claim_id: str
    title: str
    note_path: Path


@dataclass(frozen=True)
class PaperClaimRefreshSkippedItem:
    proposal_path: Path
    proposal_id: str
    title: str
    reason: str


@dataclass(frozen=True)
class PaperClaimRefreshApplyResult:
    dry_run: bool
    proposal_notes: int
    approved_items: list[PaperClaimRefreshApplyItem]
    updated_paths: list[Path]
    skipped_items: list[PaperClaimRefreshSkippedItem]


def build_paper_claim_refresh(settings: Settings, *, max_proposals: int = 50, fetch_metadata: bool = True) -> PaperClaimRefreshResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    paper_sources = [source for source in _source_snapshots(vault) if source.source_type == "papers"]
    candidates: list[PaperClaimRefreshCandidate] = []
    warnings: list[str] = []

    for source in paper_sources:
        metadata = _paper_metadata(source.path)
        for claim in source.claims:
            if not _needs_refresh(claim):
                continue
            enriched = _enrich_claim(source, claim, metadata, fetch_metadata=fetch_metadata, warnings=warnings)
            if enriched is None:
                warnings.append(f"No enrichment candidate for {source.relative_path} {claim.claim_id}")
                continue
            proposal_id = f"C{len(candidates) + 1:03d}"
            candidates.append(
                PaperClaimRefreshCandidate(
                    proposal_id=proposal_id,
                    source=source,
                    claim=claim,
                    metadata=metadata,
                    new_summary=enriched["summary"],
                    new_claim=enriched["claim"],
                    new_evidence=enriched["evidence"],
                    refresh_provider=enriched["provider"],
                )
            )
            if len(candidates) >= max_proposals:
                return PaperClaimRefreshResult(vault_path=vault, paper_sources=len(paper_sources), candidates=candidates, warnings=warnings)

    return PaperClaimRefreshResult(vault_path=vault, paper_sources=len(paper_sources), candidates=candidates, warnings=warnings)


def render_paper_claim_refresh(result: PaperClaimRefreshResult, *, max_proposals: int = 50) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""Paper Claim Refresh

Vault: {result.vault_path}
Paper sources scanned: {result.paper_sources}
Claim refresh candidates: {len(result.candidates)}
Warnings: {len(result.warnings)}

Proposals:
{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

Warnings:
{_warning_lines(result.warnings)}
"""


def write_paper_claim_refresh_note(settings: Settings, *, max_proposals: int = 50, fetch_metadata: bool = True) -> PaperClaimRefreshWriteResult:
    result = build_paper_claim_refresh(settings, max_proposals=max_proposals, fetch_metadata=fetch_metadata)
    timestamp = now_local(settings.app.timezone)
    writer = ObsidianWriter(
        settings.obsidian,
        common_module_path=settings.common.module_path,
        use_common_module=settings.common.enabled,
    )
    writer.ensure_structure()
    path = writer.write_note(
        settings.obsidian.run_dir,
        f"{timestamp.date().isoformat()}_paper-claim-refresh.md",
        render_paper_claim_refresh_note(result, checked_at=timestamp.isoformat(timespec="seconds"), max_proposals=max_proposals),
    )
    return PaperClaimRefreshWriteResult(result=result, note_path=path)


def apply_paper_claim_refresh(settings: Settings, *, dry_run: bool = True, applied_at: str = "") -> PaperClaimRefreshApplyResult:
    vault = settings.obsidian.vault_path.expanduser().resolve()
    proposal_notes = 0
    approved_items: list[PaperClaimRefreshApplyItem] = []
    updated_paths: list[Path] = []
    skipped_items: list[PaperClaimRefreshSkippedItem] = []
    proposal_paths_to_mark: set[Path] = set()

    for proposal_path in _paper_claim_refresh_notes(vault):
        proposal_notes += 1
        text = proposal_path.read_text(encoding="utf-8", errors="replace")
        records = _candidate_records(text, vault=vault)
        checked_ids = _checked_proposal_ids(text)
        for proposal_id in checked_ids:
            candidate = records.get(proposal_id)
            if candidate is None:
                skipped_items.append(PaperClaimRefreshSkippedItem(proposal_path, proposal_id, "", "candidate record not found"))
                continue
            if not candidate.source.path.exists():
                skipped_items.append(PaperClaimRefreshSkippedItem(proposal_path, proposal_id, candidate.source.title, "source note not found"))
                continue
            source_text = candidate.source.path.read_text(encoding="utf-8", errors="replace")
            updated_text, changed = _apply_candidate_to_text(source_text, candidate, applied_at=applied_at)
            if not changed:
                skipped_items.append(PaperClaimRefreshSkippedItem(proposal_path, proposal_id, candidate.source.title, "source note already refreshed or pattern not found"))
                continue
            if not dry_run:
                candidate.source.path.write_text(updated_text, encoding="utf-8")
            approved_items.append(
                PaperClaimRefreshApplyItem(
                    proposal_path=proposal_path,
                    proposal_id=proposal_id,
                    source_id=candidate.source.source_id,
                    claim_id=candidate.claim.claim_id,
                    title=candidate.source.title,
                    note_path=candidate.source.path,
                )
            )
            updated_paths.append(candidate.source.path)
            proposal_paths_to_mark.add(proposal_path)

    if not dry_run:
        for proposal_path in proposal_paths_to_mark:
            text = proposal_path.read_text(encoding="utf-8", errors="replace")
            values = {"proposal_state": "applied"}
            if applied_at:
                values["applied_at"] = applied_at
            proposal_path.write_text(_set_frontmatter_scalars(text, values), encoding="utf-8")

    return PaperClaimRefreshApplyResult(
        dry_run=dry_run,
        proposal_notes=proposal_notes,
        approved_items=approved_items,
        updated_paths=sorted(set(updated_paths), key=lambda item: item.as_posix()),
        skipped_items=skipped_items,
    )


def render_paper_claim_refresh_apply_result(result: PaperClaimRefreshApplyResult) -> str:
    title = "Paper Claim Refresh Apply Dry Run" if result.dry_run else "Paper Claim Refresh Apply"
    action = "Would update notes" if result.dry_run else "Updated notes"
    return f"""{title}

Proposal notes scanned: {result.proposal_notes}
Approved checklist items: {len(result.approved_items)}
{action}: {len(result.updated_paths)}
Skipped items: {len(result.skipped_items)}

Approved items:
{_apply_item_lines(result.approved_items)}

Updated notes:
{_path_lines(result.updated_paths)}

Skipped items:
{_skipped_item_lines(result.skipped_items)}
"""


def render_paper_claim_refresh_note(
    result: PaperClaimRefreshResult,
    *,
    checked_at: str,
    max_proposals: int = 50,
) -> str:
    shown = result.candidates[:max_proposals]
    hidden = len(result.candidates) - len(shown)
    return f"""---
type: {yaml_scalar("paper-claim-refresh")}
status: {yaml_scalar("draft")}
proposal_state: {yaml_scalar("proposed")}
generated_by: {yaml_scalar("research-agent")}
checked_at: {yaml_scalar(checked_at)}
paper_source_count: {result.paper_sources}
proposal_count: {len(result.candidates)}
warning_count: {len(result.warnings)}
---
# Paper Claim Refresh

## Summary

| metric | value |
|---|---:|
| paper sources scanned | {result.paper_sources} |
| claim refresh candidates | {len(result.candidates)} |
| warnings | {len(result.warnings)} |

## Proposals

{_proposal_lines(shown)}
{_hidden_line(hidden, max_proposals)}

## Warnings

{_warning_lines(result.warnings)}

## Review Checklist

- [ ] Confirm each refreshed paper claim is specific enough for downstream evidence.
- [ ] Apply accepted claim updates with `apply-paper-claim-refresh`.
- [ ] Rerun `paper-downstream-proposals`.

## Candidate Records

```json
{_candidate_json(result.candidates)}
```
"""


def _needs_refresh(claim: SourceClaimSnapshot) -> bool:
    claim_text = claim.claim.strip()
    evidence_text = claim.evidence.strip()
    return _is_generic_metadata(claim_text) or _is_generic_metadata(evidence_text)


def _is_generic_metadata(value: str) -> bool:
    return bool(GENERIC_METADATA_RE.match(value.strip()))


def _paper_metadata(path: Path) -> PaperSourceMetadata:
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, _body = _split_frontmatter(text)
    authors_value = frontmatter.get("authors")
    authors = [str(author) for author in authors_value] if isinstance(authors_value, list) else []
    return PaperSourceMetadata(
        doi=_frontmatter_scalar(frontmatter, "doi"),
        arxiv_id=_frontmatter_scalar(frontmatter, "arxiv_id"),
        authors=authors,
        published_at=_frontmatter_scalar(frontmatter, "published_at"),
        provider=_frontmatter_scalar(frontmatter, "source_provider"),
    )


def _enrich_claim(
    source: SourceSnapshot,
    claim: SourceClaimSnapshot,
    metadata: PaperSourceMetadata,
    *,
    fetch_metadata: bool,
    warnings: list[str],
) -> dict[str, str] | None:
    if fetch_metadata:
        external = _external_summary(source, metadata, warnings=warnings)
        if external:
            return external
    summary = _metadata_summary(source, metadata)
    if not summary or _is_generic_metadata(summary):
        return None
    return {
        "summary": summary,
        "claim": summary,
        "evidence": summary,
        "provider": "local-paper-metadata",
    }


def _metadata_summary(source: SourceSnapshot, metadata: PaperSourceMetadata) -> str:
    title = source.title.strip()
    if not title:
        return ""
    authors = _author_text(metadata.authors)
    year = _year(metadata.published_at)
    identity = _identity_text(metadata)
    topic_text = f" for `{source.topic}`" if source.topic else ""
    pieces = [title]
    work_type = _friendly_work_type(metadata.work_type)
    venue = metadata.venue.strip()
    if year:
        pieces.append(f"is a {year} {work_type}")
    else:
        pieces.append(f"is a {work_type}")
    if venue:
        pieces.append(f"in {venue}")
    if authors:
        pieces.append(f"by {authors}")
    if identity:
        pieces.append(f"identified by {identity}")
    return " ".join(pieces) + f" that should be reviewed as paper evidence{topic_text}."


def _external_summary(source: SourceSnapshot, metadata: PaperSourceMetadata, *, warnings: list[str]) -> dict[str, str] | None:
    doi = normalize_doi(metadata.doi)
    if not doi:
        return None
    records = [
        _fetch_semantic_scholar_summary(doi, warnings=warnings),
        _fetch_openalex_summary(doi, warnings=warnings),
        _fetch_crossref_summary(doi, source=source, metadata=metadata, warnings=warnings),
    ]
    records = [record for record in records if record and not _is_generic_metadata(record["summary"])]
    if not records:
        return None
    records.sort(key=_external_quality_key, reverse=True)
    best = records[0]
    summary = best["summary"]
    return {
        "summary": summary,
        "claim": summary,
        "evidence": summary,
        "provider": best["provider"],
    }


def _fetch_semantic_scholar_summary(doi: str, *, warnings: list[str]) -> dict[str, str] | None:
    encoded = urllib.parse.quote(f"DOI:{doi}", safe="")
    fields = "title,abstract,authors,year,publicationDate,venue,externalIds,url"
    url = f"https://api.semanticscholar.org/graph/v1/paper/{encoded}?fields={urllib.parse.quote(fields, safe=',')}"
    try:
        data = json.loads(_get_text(url, timeout_seconds=15))
    except Exception as exc:
        warnings.append(f"semantic-scholar: {type(exc).__name__}: {exc}")
        return None
    abstract = str(data.get("abstract") or "").strip()
    if abstract:
        return {"summary": _abstract_summary(abstract), "provider": "semantic-scholar-doi"}
    title = str(data.get("title") or "").strip()
    venue = str(data.get("venue") or "").strip()
    year = str(data.get("publicationDate") or data.get("year") or "").strip()
    authors = [
        str(author.get("name") or "").strip()
        for author in data.get("authors", [])
        if isinstance(author, dict) and str(author.get("name") or "").strip()
    ]
    summary = _metadata_sentence(title=title, work_type="paper", venue=venue, year=year, authors=authors, doi=doi)
    return {"summary": summary, "provider": "semantic-scholar-doi"} if summary else None


def _fetch_openalex_summary(doi: str, *, warnings: list[str]) -> dict[str, str] | None:
    encoded = urllib.parse.quote(f"doi:{doi}", safe=":")
    url = f"https://api.openalex.org/works/{encoded}"
    try:
        data = json.loads(_get_text(url, timeout_seconds=15))
    except Exception as exc:
        warnings.append(f"openalex: {type(exc).__name__}: {exc}")
        return None
    abstract = _openalex_abstract(data.get("abstract_inverted_index"))
    if abstract:
        return {"summary": _abstract_summary(abstract), "provider": "openalex-doi"}
    title = str(data.get("display_name") or "").strip()
    venue = ""
    primary_location = data.get("primary_location")
    if isinstance(primary_location, dict):
        source = primary_location.get("source")
        if isinstance(source, dict):
            venue = str(source.get("display_name") or "").strip()
    year = str(data.get("publication_date") or data.get("publication_year") or "").strip()
    authors: list[str] = []
    for authorship in data.get("authorships", []):
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict) and author.get("display_name"):
            authors.append(str(author["display_name"]).strip())
    work_type = str(data.get("type") or "paper").replace("-", " ")
    summary = _metadata_sentence(title=title, work_type=work_type, venue=venue, year=year, authors=authors, doi=doi)
    return {"summary": summary, "provider": "openalex-doi"} if summary else None


def _fetch_crossref_summary(doi: str, *, source: SourceSnapshot, metadata: PaperSourceMetadata, warnings: list[str]) -> dict[str, str] | None:
    encoded = urllib.parse.quote(doi, safe="")
    url = f"https://api.crossref.org/works/{encoded}"
    try:
        data = json.loads(_get_text(url, timeout_seconds=15))
    except Exception as exc:
        warnings.append(f"crossref: {type(exc).__name__}: {exc}")
        return None
    message = data.get("message") if isinstance(data, dict) else {}
    if not isinstance(message, dict):
        return None
    abstract = _strip_markup(str(message.get("abstract") or ""))
    if abstract:
        return {"summary": _abstract_summary(abstract), "provider": "crossref-doi"}
    title = _first_text(message.get("title")) or source.title
    venue = _first_text(message.get("container-title")) or metadata.venue
    year = _date_parts(message.get("published-print") or message.get("published-online") or message.get("published")) or metadata.published_at
    authors = [
        " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part).strip()
        for author in message.get("author", [])
        if isinstance(author, dict)
    ] or metadata.authors
    work_type = str(message.get("type") or metadata.work_type or "paper").replace("-", " ")
    summary = _metadata_sentence(title=title, work_type=work_type, venue=venue, year=year, authors=authors, doi=doi)
    return {"summary": summary, "provider": "crossref-doi"} if summary else None


def _apply_candidate_to_text(text: str, candidate: PaperClaimRefreshCandidate, *, applied_at: str) -> tuple[str, bool]:
    updated = text
    changed = False
    old_claim = candidate.claim.claim
    old_evidence = candidate.claim.evidence
    new_summary = candidate.new_summary
    new_claim = candidate.new_claim
    new_evidence = candidate.new_evidence
    claim_id = re.escape(candidate.claim.claim_id)
    confidence = re.escape(candidate.claim.confidence or "medium")
    category = re.escape(candidate.claim.category or "papers")

    updated, block_changed = _replace_core_summary(updated, new_summary)
    changed = changed or block_changed

    important_pattern = re.compile(
        rf"- \*\*원본:\*\* {claim_id} \({confidence}, {category}\): .+?\n"
        rf"  - \*\*한국어 번역:\*\* {claim_id} \([^)]+\): .+?(?=\n(?:- |\n##|\Z))",
        re.DOTALL,
    )
    important_replacement = (
        f"- **원본:** {candidate.claim.claim_id} ({candidate.claim.confidence}, {candidate.claim.category}): {new_claim}\n"
        f"  - **한국어 번역:** {candidate.claim.claim_id} ({_translate_to_korean(candidate.claim.confidence)}, "
        f"{_translate_to_korean(candidate.claim.category)}): {_translate_to_korean(new_claim)}"
    )
    updated, count = important_pattern.subn(important_replacement, updated, count=1)
    changed = changed or count > 0

    evidence_pattern = re.compile(
        rf"- {claim_id} 원본: .+?\n"
        rf"  - {claim_id} 한국어 번역: .+?(?=\n(?:- |\n##|\Z))",
        re.DOTALL,
    )
    evidence_replacement = f"- {candidate.claim.claim_id} 원본: {new_evidence}\n  - {candidate.claim.claim_id} 한국어 번역: {_translate_to_korean(new_evidence)}"
    updated, count = evidence_pattern.subn(evidence_replacement, updated, count=1)
    changed = changed or count > 0

    if old_claim and old_claim in updated and _is_generic_metadata(old_claim):
        updated = updated.replace(old_claim, new_claim)
        changed = True
    if old_evidence and old_evidence != old_claim and old_evidence in updated and _is_generic_metadata(old_evidence):
        updated = updated.replace(old_evidence, new_evidence)
        changed = True

    if changed:
        values = {
            "claim_refresh_provider": candidate.refresh_provider,
        }
        if applied_at:
            values["claim_refreshed_at"] = applied_at
        updated = _set_frontmatter_scalars(updated, values)
        updated = _append_refresh_history(updated, candidate, applied_at=applied_at)
    return updated, changed


def _replace_core_summary(text: str, summary: str) -> tuple[str, bool]:
    pattern = re.compile(
        r"(## Core Summary\s+\*\*원본\*\*\s+)(?P<original>[\s\S]*?)(\s+\*\*한국어 번역\*\*\s+)(?P<translated>[\s\S]*?)(?=\n## )",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return text, False
    replacement = (
        f"{match.group(1)}{summary}\n\n"
        f"**한국어 번역**\n\n{_translate_to_korean(summary)}\n"
    )
    return text[: match.start()] + replacement + text[match.end() :], True


def _append_refresh_history(text: str, candidate: PaperClaimRefreshCandidate, *, applied_at: str) -> str:
    timestamp = applied_at or "not captured"
    line = (
        f"- {timestamp}: {candidate.claim.claim_id} refreshed from generic paper metadata "
        f"using {candidate.refresh_provider}."
    )
    if "## Paper Claim Refresh History" not in text:
        return text.rstrip() + f"\n\n## Paper Claim Refresh History\n\n{line}\n"
    if line in text:
        return text
    pattern = re.compile(r"(?m)^## Paper Claim Refresh History\s*$")
    match = pattern.search(text)
    if not match:
        return text.rstrip() + f"\n\n## Paper Claim Refresh History\n\n{line}\n"
    section_start = text.find("\n", match.end())
    section_start = len(text) if section_start == -1 else section_start + 1
    return text[:section_start] + "\n" + line + text[section_start:]


def _candidate_json(candidates: list[PaperClaimRefreshCandidate]) -> str:
    return json.dumps([_candidate_to_json(candidate) for candidate in candidates], ensure_ascii=False, indent=2)


def _candidate_to_json(candidate: PaperClaimRefreshCandidate) -> dict:
    source = candidate.source
    claim = candidate.claim
    metadata = candidate.metadata
    return {
        "proposal_id": candidate.proposal_id,
        "source": {
            "path": source.relative_path,
            "topic": source.topic,
            "source_id": source.source_id,
            "source_type": source.source_type,
            "title": source.title,
            "source_url": source.source_url,
            "canonical_url": source.canonical_url,
            "checked_at": source.checked_at,
        },
        "claim": {
            "claim_id": claim.claim_id,
            "claim": claim.claim,
            "evidence": claim.evidence,
            "confidence": claim.confidence,
            "category": claim.category,
        },
        "metadata": {
            "doi": metadata.doi,
            "arxiv_id": metadata.arxiv_id,
            "authors": metadata.authors,
            "published_at": metadata.published_at,
            "provider": metadata.provider,
            "venue": metadata.venue,
            "work_type": metadata.work_type,
        },
        "new_summary": candidate.new_summary,
        "new_claim": candidate.new_claim,
        "new_evidence": candidate.new_evidence,
        "refresh_provider": candidate.refresh_provider,
    }


def _candidate_records(text: str, *, vault: Path) -> dict[str, PaperClaimRefreshCandidate]:
    match = CANDIDATE_BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        items = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(items, list):
        return {}
    records: dict[str, PaperClaimRefreshCandidate] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = _candidate_from_json(item, vault=vault)
        if candidate:
            records[candidate.proposal_id] = candidate
    return records


def _candidate_from_json(item: dict, *, vault: Path) -> PaperClaimRefreshCandidate | None:
    proposal_id = str(item.get("proposal_id") or "").strip()
    source_item = item.get("source")
    claim_item = item.get("claim")
    metadata_item = item.get("metadata")
    if not proposal_id or not isinstance(source_item, dict) or not isinstance(claim_item, dict) or not isinstance(metadata_item, dict):
        return None
    source_path = _vault_path(vault, str(source_item.get("path") or ""))
    if source_path is None:
        return None
    source = SourceSnapshot(
        path=source_path,
        relative_path=str(source_item.get("path") or "").strip(),
        topic=str(source_item.get("topic") or "").strip(),
        source_id=str(source_item.get("source_id") or "").strip(),
        source_type=str(source_item.get("source_type") or "").strip(),
        title=str(source_item.get("title") or "").strip(),
        source_url=str(source_item.get("source_url") or "").strip(),
        canonical_url=str(source_item.get("canonical_url") or "").strip(),
        checked_at=str(source_item.get("checked_at") or "").strip(),
        claims=[],
    )
    claim = SourceClaimSnapshot(
        claim_id=str(claim_item.get("claim_id") or "").strip(),
        claim=str(claim_item.get("claim") or "").strip(),
        evidence=str(claim_item.get("evidence") or "").strip(),
        confidence=str(claim_item.get("confidence") or "").strip(),
        category=str(claim_item.get("category") or "").strip(),
    )
    metadata = PaperSourceMetadata(
        doi=str(metadata_item.get("doi") or "").strip(),
        arxiv_id=str(metadata_item.get("arxiv_id") or "").strip(),
        authors=[str(author) for author in metadata_item.get("authors") or [] if str(author).strip()],
        published_at=str(metadata_item.get("published_at") or "").strip(),
        provider=str(metadata_item.get("provider") or "").strip(),
        venue=str(metadata_item.get("venue") or "").strip(),
        work_type=str(metadata_item.get("work_type") or "").strip(),
    )
    new_summary = str(item.get("new_summary") or "").strip()
    new_claim = str(item.get("new_claim") or "").strip()
    new_evidence = str(item.get("new_evidence") or "").strip()
    refresh_provider = str(item.get("refresh_provider") or "").strip()
    if not source.source_id or not source.title or not claim.claim_id or not new_claim:
        return None
    return PaperClaimRefreshCandidate(
        proposal_id=proposal_id,
        source=source,
        claim=claim,
        metadata=metadata,
        new_summary=new_summary or new_claim,
        new_claim=new_claim,
        new_evidence=new_evidence or new_claim,
        refresh_provider=refresh_provider or "unknown",
    )


def _checked_proposal_ids(text: str) -> list[str]:
    return [match.group("proposal_id") for match in CLAIM_REFRESH_CHECK_RE.finditer(text) if match.group("state").strip().lower() == "x"]


def _paper_claim_refresh_notes(vault: Path) -> list[Path]:
    paths: list[Path] = []
    for path in _markdown_files(vault):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body = _split_frontmatter(text)
        if _frontmatter_scalar(frontmatter, "type") == "paper-claim-refresh":
            paths.append(path)
    return sorted(paths)


def _proposal_lines(candidates: list[PaperClaimRefreshCandidate]) -> str:
    if not candidates:
        return "- None."
    return "\n".join(_proposal_line(candidate) for candidate in candidates)


def _proposal_line(candidate: PaperClaimRefreshCandidate) -> str:
    return (
        f"- [ ] {candidate.proposal_id} Refresh {candidate.claim.claim_id} from {candidate.source.source_id} "
        f"[{candidate.source.title}]({candidate.source.preferred_url}) "
        f"(provider: {candidate.refresh_provider})"
    )


def _apply_item_lines(items: list[PaperClaimRefreshApplyItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.source_id}/{item.claim_id}: {item.title} -> {item.note_path}" for item in items)


def _skipped_item_lines(items: list[PaperClaimRefreshSkippedItem]) -> str:
    if not items:
        return "- None."
    return "\n".join(f"- {item.proposal_id} {item.title}: {item.reason}" for item in items)


def _path_lines(paths: list[Path]) -> str:
    if not paths:
        return "- None."
    return "\n".join(f"- {path}" for path in paths)


def _warning_lines(warnings: list[str]) -> str:
    if not warnings:
        return "- None."
    return "\n".join(f"- {warning}" for warning in warnings)


def _hidden_line(hidden: int, max_proposals: int) -> str:
    if hidden <= 0:
        return ""
    return f"\n... {hidden} more proposal(s) hidden by --max-proposals={max_proposals}."


def _author_text(authors: list[str]) -> str:
    cleaned = [author for author in authors if author]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{cleaned[0]} et al."


def _metadata_sentence(
    *,
    title: str,
    work_type: str,
    venue: str,
    year: str,
    authors: list[str],
    doi: str,
) -> str:
    title = title.strip()
    if not title:
        return ""
    author_text = _author_text(authors)
    year_text = _year(year)
    friendly_type = _friendly_work_type(work_type)
    parts = [title]
    if year_text:
        parts.append(f"is a {year_text} {friendly_type}")
    else:
        parts.append(f"is a {friendly_type}")
    if venue:
        parts.append(f"in {venue}")
    if author_text:
        parts.append(f"by {author_text}")
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        parts.append(f"identified by DOI {normalized_doi}")
    return " ".join(parts) + "."


def _friendly_work_type(value: str) -> str:
    text = value.strip().lower().replace("_", " ").replace("-", " ")
    if not text:
        return "paper or book chapter"
    if "book chapter" in text or text == "chapter":
        return "book chapter"
    if "proceedings" in text:
        return "conference paper"
    if "journal" in text or "article" in text:
        return "paper"
    if text == "book":
        return "book"
    return text


def _abstract_summary(abstract: str) -> str:
    cleaned = _strip_markup(abstract)
    if not cleaned:
        return ""
    sentence = _first_sentence(cleaned)
    return sentence or cleaned[:360].rstrip()


def _first_sentence(text: str) -> str:
    match = re.search(r"^(.{80,360}?[.!?])(?:\s|$)", text.strip())
    return match.group(1).strip() if match else ""


def _strip_markup(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _openalex_abstract(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    positions: dict[int, str] = {}
    for word, indexes in value.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int):
                positions[index] = str(word)
    return " ".join(positions[index] for index in sorted(positions))


def _first_text(value: object) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    if isinstance(value, str):
        return value.strip()
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


def _external_quality_key(record: dict[str, str]) -> tuple[int, int]:
    provider = record.get("provider", "")
    summary = record.get("summary", "")
    abstract_score = 1 if len(summary) > 120 and "identified by DOI" not in summary else 0
    provider_score = {
        "semantic-scholar-doi": 3,
        "openalex-doi": 2,
        "crossref-doi": 1,
    }.get(provider, 0)
    return abstract_score, provider_score


def _year(value: str) -> str:
    match = re.search(r"\d{4}", value or "")
    return match.group(0) if match else ""


def _identity_text(metadata: PaperSourceMetadata) -> str:
    doi = normalize_doi(metadata.doi)
    if doi:
        return f"DOI {doi}"
    if metadata.arxiv_id:
        return f"arXiv {metadata.arxiv_id}"
    return ""


def _vault_path(vault: Path, relative: str) -> Path | None:
    clean = relative.strip().strip("/")
    if not clean:
        return None
    candidate = (vault / clean).resolve()
    if candidate != vault and vault not in candidate.parents:
        return None
    return candidate
